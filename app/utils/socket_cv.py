"""Vision artificial determinista para sockets CPU (cv2/numpy).

Modulo independiente del resto de la app (sin imports de app), mismo
principio que image_ops.py: evita dependencias circulares.

Por que CV y no solo VLM:
  - Contar pines e identificar el socket son tareas de MEDICION, no de
    percepcion. Un VLM "estima" (dice 1700 para una placa de 1151) y puede
    alucinar defectos. El CV mide exacto.
  - El socket es la region con el campo de pines denso y regular: se
    localiza por umbral adaptativo + componentes conexos + densidad 2D
    (histograma + nucleo gaussiano escalado al pitch de pines), sin pedirle
    coordenadas al VLM.
  - Pines faltantes/doblados son DESVIACIONES de la reticula regular: se
    detectan de forma determinista (celda interior vacia con vecinos, o
    componente anomalo alineado con una celda). Sin alucinaciones.

El VLM queda como VERIFICADOR (ver ai_proxy): confirma la medicion y aporta
descripciones; nunca define el conteo ni el tipo de socket.

Convencion de coordenadas: todas las cajas (deteccion, defectos) son
fracciones 0.0-1.0 de la IMAGEN COMPLETA que se le pasa a la funcion.
`analyze_pins` mide la imagen completa restringiendo la medicion a la region
`bbox_frac` (o autodetectandola por densidad si no se da). Los centros de
pin se guardan en fracciones para que el render use la escala que quiera.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from PIL import Image

# Referencias de pines por tipo de socket (coherente con SOCKET_TYPES en
# validators.py). En Intel LGA el numero del socket ES el conteo de pines,
# por lo que el conteo deterministico identifica el tipo sin ambiguedad.
SOCKET_PIN_REFS: dict[int, str] = {
    1151: "INTEL_LGA1151",
    1200: "INTEL_LGA1200",
    1331: "AMD_AM4",
    1700: "INTEL_LGA1700",
    1718: "AMD_AM5",
}

MIN_CONFIDENT_PINS = 80  # por debajo no hay suficiente senal para medir
MIN_SOCKET_PINS = 300  # conteo rapido para considerar una region un socket
MISSING_EDGE_MARGIN = 1  # celdas de borde nunca reportan pin faltante (notches)

# Rango de areas (en mediana) que puede tener un componente y seguir siendo
# candidato a pin/anomalia; fuera de eso es PCB, frame o ruido grueso.
_MIN_AREA_FRAC = 0.1
_MAX_AREA_FRAC = 15.0


@dataclass
class SocketDetection:
    """Region del campo de pines localizada en la imagen."""

    # [x1, y1, x2, y2] como fracciones 0.0-1.0 de la imagen completa
    bbox_frac: tuple[float, float, float, float]
    # Estimacion rapida del numero de pines dentro de la region
    pin_estimate: int
    confidence: float


@dataclass
class CvDefect:
    """Defecto localizado por CV, con caja en fracciones de la imagen completa."""

    description: str
    bbox_frac: list[float]


@dataclass
class PinAnalysis:
    """Resultados de la medicion determinista del socket."""

    pin_count: int
    socket_type: str
    type_confidence: float
    grid_rows: int
    grid_cols: int
    defects: list[CvDefect] = field(default_factory=list)
    confidence: float = 0.0
    # Centros de pines en FRACCIONES 0.0-1.0 de la imagen completa (para el
    # render de evidencia, que puede usar otra escala)
    pin_centers: tuple[list[float], list[float]] = ([], [])


def _to_gray(img: Image.Image) -> np.ndarray:
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.uint8)


def _scale_to(img: Image.Image, target: int) -> Image.Image:
    """Amplia (nunca reduce) hasta que el lado mayor alcance `target`."""
    longest = max(img.size)
    if longest >= target:
        return img
    s = target / longest
    return img.resize(
        (max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS
    )


def _adaptive_mask(gray: np.ndarray, block: int = 51, c: int = 10) -> np.ndarray:
    """Mascara de pines para la IMAGEN COMPLETA (iluminacion no uniforme).

    C negativo: el umbral es la media local + c, asi SOLO los pixeles mas
    brillantes que su vecindad (los pines) quedan blancos y un fondo
    uniforme queda negro. Con C positivo, una region uniforme siempre
    cumple `src > media - c` y el fondo entero se vuelve un mar blanco
    que se fusiona con el campo de pines (el socket deja de localizarse).
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    mask = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, -c
    )
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    return mask


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int, float, float]]:
    """Componentes conexos con area >= 3: (id, x, y, w, h, area, cx, cy)."""
    num, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out: list[tuple[int, int, int, int, int, float, float]] = []
    for i in range(1, num):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 3:
            continue
        out.append((i, x, y, w, h, area, float(cents[i][0]), float(cents[i][1])))
    return out


def _grid_peaks(values: np.ndarray) -> tuple[Optional[np.ndarray], int]:
    """Centros de filas/columnas de la reticula por picos en la proyeccion 1D.

    Devuelve (centros, n_picos) o (None, 0) si la estructura no es una
    reticula regular (no es un socket).

    Detalles que importan (validados contra placa real LGA1151):
      - Umbral 0.25*max (no 0.35): con 0.35 se perdian columnas de pines
        parcialmente iluminados y la reticula salia 50x50 en vez de 41x41.
      - Fusion de picos separados por menos de 0.6*pitch mediano: un pin
        partido en dos componentes (glare) producia un pico doble que
        inflaba el numero de columnas.
      - Centro ponderado sobre el grupo fusionado, no sobre el pico.
    """
    if values.size < 10:
        return None, 0
    span = float(values.max() - values.min())
    if span < 8:
        return None, 0
    bins = min(400, max(60, int(span / 2)))
    hist, edges = np.histogram(values, bins=bins)
    sm = np.convolve(hist.astype(np.float64), np.ones(3) / 3.0, mode="same")
    maxv = float(sm.max())
    if maxv <= 0:
        return None, 0
    thr = 0.25 * maxv
    min_gap = max(2, int(bins * 0.005))
    peaks: list[int] = []
    i = 0
    n = len(sm)
    while i < n:
        left = sm[i - 1] if i > 0 else -np.inf
        right = sm[i + 1] if i < n - 1 else -np.inf
        # >= a la izquierda y ESTRICTAMENTE > a la derecha: el suavizado de 3
        # puntos convierte cada pico en una meseta plana de 3 bins iguales y,
        # con comparacion no estricta a ambos lados, cada meseta producia 2
        # picos (una reticula de 20 columnas daba 37-38 "columnas"). Con la
        # estricta a la derecha la meseta produce un solo pico (donde baja).
        if sm[i] >= thr and sm[i] >= left and sm[i] > right:
            peaks.append(i)
            i += min_gap
        else:
            i += 1
    if len(peaks) < 4 or len(peaks) > 200:
        return None, 0
    # Fusion de picos separados por menos de 0.6 * pitch mediano (doble pico
    # de un pin partido en dos componentes por glare)
    groups: list[list[int]] = [[peaks[0]]]
    diffs = np.diff(peaks)
    if len(diffs):
        pitch = float(np.median(diffs))
        for p in peaks[1:]:
            if p - groups[-1][-1] < 0.6 * pitch:
                groups[-1].append(p)
            else:
                groups.append([p])
    centers: list[float] = []
    for group in groups:
        a, b = max(0, min(group) - 2), min(n, max(group) + 3)
        wts = sm[a:b]
        # Centros de los bins [a, b): (edges[k] + edges[k+1]) / 2
        mid = (edges[a:b] + edges[a + 1 : b + 1]) / 2.0
        total = float(wts.sum())
        centers.append(
            float(np.average(mid, weights=wts)) if total > 0 else float(mid.mean())
        )
    return np.array(centers, dtype=np.float64), len(centers)


def _median_nn(pts: np.ndarray, max_n: int = 1200) -> float:
    """Distancia mediana al vecino mas cercano (proxy del pitch de pines).

    Se subsamplaea para no costar O(n^2) en campos de 1100+ pines.
    """
    if len(pts) > max_n:
        pts = pts[:: len(pts) // max_n]
    n = len(pts)
    nn = np.empty(n)
    for s in range(0, n, 400):
        e = min(n, s + 400)
        d = np.linalg.norm(pts[s:e, None, :] - pts[None, :, :], axis=2)
        for k in range(e - s):
            d[k, s + k] = np.inf
        nn[s:e] = d.min(axis=1)
    return float(np.median(nn[nn > 0])) if (nn > 0).any() else 0.0


def _area_stats(comps: list) -> Optional[tuple[float, float, float]]:
    """(mediana, lo, hi) de areas de componentes; None si la senal es minima.

    `hi` combina 1.6*mediana y el percentil 99 (capado en 15*mediana): en una
    placa real hay PCB brillante junto al socket y un cap fijo de 1.6*mediana
    clasificaba los pines mas iluminados como anomalias (pines "grandes"
    brillantes que de todos modos hay que contar).
    """
    areas = np.array([c[5] for c in comps], dtype=np.float64)
    med = float(np.median(areas))
    if med < 2:
        return None
    lo = 0.2 * med
    hi = min(max(1.6 * med, float(np.percentile(areas, 99))), 15.0 * med)
    return med, lo, hi


def _select_dots(comps: list, lo: float, hi: float) -> list:
    """Componentes que parecen pines individuales (tamano relativo + forma)."""
    return [
        c
        for c in comps
        if lo <= c[5] <= hi and max(c[3], c[4]) / max(1, min(c[3], c[4])) <= 3.0
    ]


def _density_bbox(
    gray: np.ndarray, dots: list
) -> Optional[tuple[float, float, float, float]]:
    """Bbox ajustado (fracciones) de la region mas densa de puntos.

    Histograma 2D (bins de ~8px) + desenfoque gaussiano con NUCLEO ESCALADO a
    la distancia entre pines (mediana del vecino mas cercano). Un nucleo fijo
    (25px) fusionaba el campo de pines con el PCB de al lado en fotos reales
    (el bbox crecia a media imagen); un nucleo adaptativo deja el campo solo.
    """
    h, w = gray.shape
    xs = np.array([c[6] for c in dots])
    ys = np.array([c[7] for c in dots])
    # range=IMAGEN completa: por defecto numpy usa el rango de los datos y el
    # mapeo bin->pixel quedaria estirado (bbox de toda la imagen en lugar del
    # campo de pines).
    hist, _, _ = np.histogram2d(
        ys, xs, bins=[h // 8 + 1, w // 8 + 1], range=[[0, h], [0, w]]
    )
    pitch = _median_nn(np.column_stack([xs, ys]))
    kb = min(25, 2 * max(4, int(round(1.2 * pitch / 8))) + 1)
    sm = cv2.GaussianBlur(hist.astype(np.float32), (kb, kb), 0)
    m = (sm > 0.4 * float(sm.max())).astype(np.uint8)
    n, _labels, stats, _cents = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = 1 + int(np.argmax(areas))
    x, y, ww, hh = (int(v) for v in stats[best][:4])
    bsx = w / (w // 8 + 1)
    bsy = h / (h // 8 + 1)
    x1 = max(0.0, (x - 1) * bsx)
    y1 = max(0.0, (y - 1) * bsy)
    x2 = min(float(w), (x + ww + 1) * bsx)
    y2 = min(float(h), (y + hh + 1) * bsy)
    return (x1 / w, y1 / h, x2 / w, y2 / h)


def find_socket(img: Image.Image) -> Optional[SocketDetection]:
    """Localiza el campo de pines del socket en la imagen completa.

    Estrategia: umbral adaptativo + componentes conexos; los componentes con
    forma/tamano de pin forman la densidad 2D y el bbox es la region mas
    densa (no un blob morfologico, que en fotos reales se funde con el PCB).
    El candidato se valida con un conteo rapido (>= MIN_SOCKET_PINS) y una
    reticula regular (residuos de los puntos respecto a los picos).
    Capacitores, chips y heatsinks no forman ese campo denso y regular.
    Devuelve None si no se localiza con confianza (el pipeline degrada a
    localizacion por VLM).
    """
    work = _scale_to(img, 1000)
    gray = _to_gray(work)
    h, w = gray.shape
    if h < 120 or w < 120:
        return None
    mask = _adaptive_mask(gray, block=51, c=10)
    comps = _components(mask)
    if len(comps) < MIN_CONFIDENT_PINS:
        return None
    stats = _area_stats(comps)
    if stats is None:
        return None
    _med, lo, hi = stats
    dots = _select_dots(comps, lo, hi)
    if len(dots) < MIN_CONFIDENT_PINS:
        return None
    bbox_frac = _density_bbox(gray, dots)
    if bbox_frac is None:
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox_frac)
    inside = [
        c for c in dots if x1 * w <= c[6] <= x2 * w and y1 * h <= c[7] <= y2 * h
    ]
    if len(inside) < MIN_SOCKET_PINS:
        return None
    xs = np.array([c[6] for c in inside])
    ys = np.array([c[7] for c in inside])
    col_c, n_cols = _grid_peaks(xs)
    row_c, n_rows = _grid_peaks(ys)
    if col_c is None or row_c is None:
        return None
    dx = float(np.median(np.diff(col_c))) if n_cols > 1 else 0.0
    dy = float(np.median(np.diff(row_c))) if n_rows > 1 else 0.0
    if dx <= 0 or dy <= 0:
        return None
    cell = min(dx, dy)
    ci = np.argmin(np.abs(xs[:, None] - col_c[None, :]), axis=1)
    ri = np.argmin(np.abs(ys[:, None] - row_c[None, :]), axis=1)
    grid_ok = float(np.mean(np.hypot(xs - col_c[ci], ys - row_c[ri]) < 0.6 * cell))
    if grid_ok < 0.80:
        return None
    return SocketDetection(
        bbox_frac=tuple(round(float(v), 5) for v in bbox_frac),
        pin_estimate=len(inside),
        confidence=float(min(0.95, 0.55 + 0.4 * grid_ok)),
    )


def match_socket_type(count: int) -> tuple[str, float]:
    """Tipo de socket por el conteo de pines (referencia mas cercana)."""
    if count <= 0:
        return "UNKNOWN", 0.0
    best_ref = min(SOCKET_PIN_REFS, key=lambda ref: abs(count - ref))
    best_type = SOCKET_PIN_REFS[best_ref]
    diff = abs(count - best_ref)
    if diff <= 10:
        return best_type, 0.97
    if diff <= 30:
        return best_type, 0.75
    if diff <= 60:
        return best_type, 0.5
    return "UNKNOWN", 0.3


def analyze_pins(
    img: Image.Image,
    bbox_frac: Optional[tuple[float, float, float, float]] = None,
) -> Optional[PinAnalysis]:
    """Medicion determinista del socket sobre la IMAGEN COMPLETA.

    La medicion se restringe a la region `bbox_frac` (la de find_socket o la
    localizada por el VLM); si no se da, la region se autodetecta por
    densidad. Todas las salidas (bboxes de defecto, centros de pin) son
    fracciones de la imagen completa.

    1. Detecta pines individuales (umbral adaptativo + componentes conexos +
       filtro de tamano/forma RELATIVO a la mediana).
    2. Reticula por picos 1D (con fusion de picos parciales) y clasifica:
          - Pines FALTANTES: celda interior vacia con sus 4 vecinos ocupados
            y SIN componente en la celda (los notches reales estan en el
            borde y NUNCA generan falsos; un componente alargado que cubre
            la celda no es un pin faltante).
          - Pines DOBLADOS/DEFORMADOS: componente anomalo alineado con una
            celda interior (fragmento compacto o alargado en celda ocupada =
            doblado; en celda vacia = deformado, se cuenta una vez y ocupa
            la celda para que la busqueda de faltantes no lo repita).
          - Ruido: puntos aislados sin vecinos ortogonales (vias PCB) y
            fragmentos de pin partido (singleton fuera de su nodo junto a
            una celda con doble punto) se excluyen del conteo.
    3. Conteo -> tipo de socket por referencia.
    Devuelve None si la medicion no es confiable (el pipeline degrada al
    analisis clasico por VLM).
    """
    work = _scale_to(img, 1000)
    gray = _to_gray(work)
    h, w = gray.shape
    if h < 60 or w < 60:
        return None
    mask = _adaptive_mask(gray, block=51, c=10)
    comps = _components(mask)
    if len(comps) < MIN_CONFIDENT_PINS:
        return None

    if bbox_frac is None:
        stats = _area_stats(comps)
        if stats is None:
            return None
        _med, lo, hi = stats
        dots = _select_dots(comps, lo, hi)
        if len(dots) < MIN_CONFIDENT_PINS:
            return None
        bbox_frac = _density_bbox(gray, dots)
        if bbox_frac is None:
            return None

    fx1, fy1, fx2, fy2 = (float(v) for v in bbox_frac)
    X1, Y1, X2, Y2 = fx1 * w, fy1 * h, fx2 * w, fy2 * h
    comps = [c for c in comps if X1 <= c[6] <= X2 and Y1 <= c[7] <= Y2]
    if len(comps) < MIN_CONFIDENT_PINS:
        return None
    stats = _area_stats(comps)
    if stats is None:
        return None
    med, lo, hi = stats

    def aspect(c: tuple) -> float:
        return max(c[3], c[4]) / max(1, min(c[3], c[4]))

    def extent(c: tuple) -> float:
        return c[5] / float(c[3] * c[4])

    dots = _select_dots(comps, lo, hi)
    if len(dots) < MIN_CONFIDENT_PINS:
        return None

    col_c, n_cols = _grid_peaks(np.array([c[6] for c in dots]))
    row_c, n_rows = _grid_peaks(np.array([c[7] for c in dots]))
    if col_c is None or row_c is None:
        return None
    dx = float(np.median(np.diff(col_c))) if n_cols > 1 else 0.0
    dy = float(np.median(np.diff(row_c))) if n_rows > 1 else 0.0
    if dx <= 0 or dy <= 0:
        return None
    cell = min(dx, dy)

    xs = np.array([c[6] for c in dots])
    ys = np.array([c[7] for c in dots])
    ci = np.argmin(np.abs(xs[:, None] - col_c[None, :]), axis=1)
    ri = np.argmin(np.abs(ys[:, None] - row_c[None, :]), axis=1)
    grid_ok = float(np.mean(np.hypot(xs - col_c[ci], ys - row_c[ri]) < 0.6 * cell))
    if grid_ok < 0.80:
        return None

    # Clasificacion de los componentes que NO son dots:
    #  - "bright": mas grande que `hi` pero redondo y solido -> pin que el
    #    filtro de tamano descarto (se cuenta como pin).
    #  - "anomaly": masa compacta sin circularidad (cabeza doblada),
    #    fragmento alargado, o fragmento pequeno y solido -> candidato a
    #    doblado/deformado.
    dot_ids = {id(c) for c in dots}
    bright_pins: list = []
    anomalies: list = []
    for c in comps:
        if id(c) in dot_ids:
            continue
        if not (
            _MIN_AREA_FRAC * med <= c[5] <= _MAX_AREA_FRAC * med
            and max(c[3], c[4]) <= 2.5 * cell
        ):
            continue
        if c[5] > hi and aspect(c) <= 1.8 and extent(c) >= 0.65:
            bright_pins.append(c)
        elif (
            (c[5] >= 1.2 * med and extent(c) < 0.6)
            or aspect(c) >= 2.0
            or (c[5] < lo and extent(c) >= 0.65)
        ):
            anomalies.append(c)

    all_dots = dots + bright_pins
    ax = np.array([c[6] for c in all_dots])
    ay = np.array([c[7] for c in all_dots])
    aci = np.argmin(np.abs(ax[:, None] - col_c[None, :]), axis=1)
    ari = np.argmin(np.abs(ay[:, None] - row_c[None, :]), axis=1)
    occupied: set[tuple[int, int]] = set(zip(aci.tolist(), ari.tolist()))

    # Punto aislado sin NINGUN vecino ortogonal ocupado: ruido (via/PCB),
    # no pin. (En un campo denso todo pin sano tiene 4 vecinos.)
    def n_orth(c: int, r: int) -> int:
        return sum(
            1 for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if (c + dc, r + dr) in occupied
        )

    zero = {k for k in occupied if n_orth(*k) == 0}

    # Pin partido en dos componentes: dos puntos en una misma celda. Si una
    # celda adyacente tiene doble punto y uno de sus puntos esta cerca del
    # singleton, este es la otra mitad del pin partido (se descarta para no
    # recuentarlo). Solo se evaluan singletons ALINEADOS con su nodo
    # (s_res < 0.45*cell): un punto lejano a su nodo no es un pin de esa
    # celda. Validado en foto real LGA1151: sin este descarte el conteo
    # sube a 1161 (con el, 1155).
    bycell: dict[tuple[int, int], list] = {}
    for k, c in enumerate(all_dots):
        bycell.setdefault((int(aci[k]), int(ari[k])), []).append(c)
    drop: set[tuple[int, int]] = set()
    for (c, r), lst in bycell.items():
        if len(lst) != 1 or (c, r) in zero:
            continue
        s_res = float(np.hypot(lst[0][6] - col_c[c], lst[0][7] - row_c[r]))
        if s_res >= 0.45 * cell:
            continue
        for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb = (c + dc, r + dr)
            if nb in bycell and len(bycell[nb]) >= 2:
                for o in bycell[nb]:
                    if float(np.hypot(lst[0][6] - o[6], lst[0][7] - o[7])) < 0.65 * cell:
                        drop.add((c, r))
                        break
            if (c, r) in drop:
                break
    kept = occupied - zero - drop

    defects: list[CvDefect] = []

    def bbox_around(cx: float, cy: float, half: float) -> list[float]:
        return [
            round(max(0.0, (cx - half) / w), 5),
            round(max(0.0, (cy - half) / h), 5),
            round(min(1.0, (cx + half) / w), 5),
            round(min(1.0, (cy + half) / h), 5),
        ]

    def cell_has_component(cx: float, cy: float) -> bool:
        x0, x1b = cx - 0.5 * cell, cx + 0.5 * cell
        y0, y1b = cy - 0.5 * cell, cy + 0.5 * cell
        return any(
            c[1] <= x1b and c[1] + c[3] >= x0
            and c[2] <= y1b and c[2] + c[4] >= y0
            for c in comps
        )

    # Pines DOBLADOS/DEFORMADOS: componente anomalo alineado con una celda
    # interior de la reticula. Si la celda ya tiene un pin, el componente es
    # la parte del pin que sobresale (no se recuenta); si la celda esta vacia,
    # el componente ES el pin deformado (se cuenta y ocupa la celda).
    for c in anomalies:
        col = int(np.argmin(np.abs(c[6] - col_c)))
        row = int(np.argmin(np.abs(c[7] - row_c)))
        if abs(c[6] - col_c[col]) > 0.8 * cell or abs(c[7] - row_c[row]) > 0.8 * cell:
            continue
        if col <= 0 or col >= n_cols - 1 or row <= 0 or row >= n_rows - 1:
            continue  # cerca del borde: brackets/molduras, no pines
        if (col, row) in occupied:
            defects.append(
                CvDefect(
                    "Pin doblado (pin saliendo de su posicion en la reticula)",
                    bbox_around(c[6], c[7], max(c[3], c[4]) * 0.75),
                )
            )
        else:
            occupied.add((col, row))
            kept.add((col, row))
            defects.append(
                CvDefect(
                    "Pin deformado (forma irregular en su celda de la reticula)",
                    bbox_around(c[6], c[7], max(c[3], c[4]) * 0.75),
                )
            )

    # Pines FALTANTES: celda interior vacia con los 4 vecinos ocupados.
    # Las celdas de borde se ignoran: los notches de fabrica (esquinas,
    # muesca de alineacion) estan siempre cerca del borde. Un componente
    # (aunque sea anomalo) que cubra la celda impide reportarla: en placas
    # reales hay fragmentos alargados de PCB que cubren varias celdas.
    for r in range(MISSING_EDGE_MARGIN, n_rows - MISSING_EDGE_MARGIN):
        for c in range(MISSING_EDGE_MARGIN, n_cols - MISSING_EDGE_MARGIN):
            if (c, r) in occupied:
                continue
            if not all(
                (c + dc, r + dr) in occupied
                for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1))
            ):
                continue
            cx, cy = float(col_c[c]), float(row_c[r])
            if cell_has_component(cx, cy):
                continue
            defects.append(
                CvDefect(
                    "Pin faltante (celda vacia en el interior de la reticula)",
                    bbox_around(cx, cy, cell * 0.9),
                )
            )

    count = len(kept)

    socket_type, type_conf = match_socket_type(count)
    confidence = round(
        min(0.97, 0.5 + 0.45 * grid_ok + (0.05 if socket_type != "UNKNOWN" else 0.0)),
        3,
    )
    return PinAnalysis(
        pin_count=int(count),
        socket_type=socket_type,
        type_confidence=type_conf,
        grid_rows=int(n_rows),
        grid_cols=int(n_cols),
        defects=defects,
        confidence=confidence,
        pin_centers=([float(v) for v in ax / w], [float(v) for v in ay / h]),
    )


def render_analysis(img: Image.Image, analysis: PinAnalysis) -> bytes:
    """Evidencia visual de la medicion CV: pines en verde, defectos en rojo.

    Se dibuja sobre la IMAGEN COMPLETA (los centros de pin y las cajas de
    defecto son fracciones de ella) y se guarda como 04_cv_analysis.png para
    que el operador vea EXACTAMENTE que pines midio el sistema
    (trazabilidad de la medicion).
    """
    work = _scale_to(img, 1600)
    rgb = np.asarray(work.convert("RGB"), dtype=np.uint8)
    canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = canvas.shape[:2]
    xs, ys = analysis.pin_centers
    for fx, fy in zip(xs, ys):
        cv2.circle(canvas, (int(fx * w), int(fy * h)), 2, (0, 200, 0), 1, cv2.LINE_AA)
    for d in analysis.defects:
        x1, y1, x2, y2 = d.bbox_frac
        cv2.rectangle(
            canvas,
            (int(x1 * w), int(y1 * h)),
            (int(x2 * w), int(y2 * h)),
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        return b""
    return buf.tobytes()
