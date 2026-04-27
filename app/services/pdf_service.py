"""
PDF corporativo ASTECO — diseno profesional oscuro.
Fondo azul marino, tipografia blanca, badges de color, layout limpio.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

from app.services.excel_service import DailyReportRow, WeeklyReportRow

# ── Paleta oscura profesional ──────────────────────────────────────────────────
BG_DARK    = colors.HexColor("#0A1628")   # fondo principal
BG_CARD    = colors.HexColor("#132040")   # tarjetas / secciones
BG_ROW_A   = colors.HexColor("#0D1E35")   # fila impar tabla
BG_ROW_B   = colors.HexColor("#11243F")   # fila par tabla
BG_HEADER  = colors.HexColor("#0A1628")   # encabezado tabla

YELLOW     = colors.HexColor("#F5A623")   # acento dorado ASTECO
BLUE_ACCENT= colors.HexColor("#3D8BFF")   # azul brillante
WHITE      = colors.HexColor("#FFFFFF")
GRAY_LIGHT = colors.HexColor("#A8B8CC")
GRAY_MID   = colors.HexColor("#6B7F96")
BORDER     = colors.HexColor("#1E3050")

# Colores de estado (badge sobre fondo oscuro)
S_GREEN_BG = colors.HexColor("#0D3D20");  S_GREEN_FG = colors.HexColor("#4CD97B")
S_BLUE_BG  = colors.HexColor("#0D2A4A");  S_BLUE_FG  = colors.HexColor("#5BB3FF")
S_ORANGE_BG= colors.HexColor("#3D2000");  S_ORANGE_FG= colors.HexColor("#FFB347")
S_RED_BG   = colors.HexColor("#3D0D0D");  S_RED_FG   = colors.HexColor("#FF6B6B")
S_GRAY_BG  = colors.HexColor("#1A2840");  S_GRAY_FG  = colors.HexColor("#8899AA")

STATUS_MAP = {
    "entregado":      ("Entregada",            S_GREEN_BG,  S_GREEN_FG),
    "en_ruta_entrega":("En Proceso Entrega",   S_BLUE_BG,   S_BLUE_FG),
    "en_transito":    ("En Despacho",          S_BLUE_BG,   S_BLUE_FG),
    "recogido":       ("Recogida",             S_BLUE_BG,   S_BLUE_FG),
    "registrado":     ("Registrada",           S_GRAY_BG,   S_GRAY_FG),
    "novedad":        ("Novedad",              S_ORANGE_BG, S_ORANGE_FG),
    "devuelto":       ("En Dev.",              S_RED_BG,    S_RED_FG),
    "fallido":        ("No Entregada",         S_RED_BG,    S_RED_FG),
    "cerrado":        ("Cerrada",              S_GRAY_BG,   S_GRAY_FG),
    "desconocido":    ("Pendiente",            S_GRAY_BG,   S_GRAY_FG),
}

def _s(status):
    return STATUS_MAP.get(status, ("Pendiente", S_GRAY_BG, S_GRAY_FG))


# ── Canvas con fondo completo ──────────────────────────────────────────────────

def _canvas_cb(report_title: str, period_label: str, generated_at: datetime):
    ts  = generated_at.strftime("%d/%m/%Y  %H:%M")
    W, H = A4

    def draw(canvas, doc):
        canvas.saveState()

        # ── Fondo azul marino completo ──────────────────────────────────────
        canvas.setFillColor(BG_DARK)
        canvas.rect(0, 0, W, H, fill=1, stroke=0)

        # ── Franja superior decorativa ──────────────────────────────────────
        canvas.setFillColor(BG_CARD)
        canvas.rect(0, H - 3.2*cm, W, 3.2*cm, fill=1, stroke=0)

        # Linea dorada debajo de la franja
        canvas.setFillColor(YELLOW)
        canvas.rect(0, H - 3.2*cm - 3, W, 3, fill=1, stroke=0)

        # Barra lateral izquierda dorada
        canvas.setFillColor(YELLOW)
        canvas.rect(0, 0, 4, H, fill=1, stroke=0)

        # ── Logo / empresa ──────────────────────────────────────────────────
        # Nombre empresa
        canvas.setFont("Helvetica-Bold", 15)
        canvas.setFillColor(YELLOW)
        canvas.drawString(2.2*cm, H - 1.7*cm, "ASTECO")

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY_LIGHT)
        canvas.drawString(2.2*cm, H - 2.2*cm, "Sistema de Seguimiento de Guias TCC")

        # Titulo del reporte (derecha)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.setFillColor(WHITE)
        canvas.drawRightString(W - 2*cm, H - 1.7*cm, report_title)

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY_LIGHT)
        canvas.drawRightString(W - 2*cm, H - 2.2*cm, period_label)

        # ── Footer ──────────────────────────────────────────────────────────
        # Linea separadora
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.8)
        canvas.line(2*cm, 1.9*cm, W - 2*cm, 1.9*cm)

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRAY_MID)
        canvas.drawString(2*cm, 1.3*cm, f"Generado: {ts}   |   ASTECO S.A.S.   |   Confidencial - Uso Interno")
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(YELLOW)
        canvas.drawRightString(W - 2*cm, 1.3*cm, f"{doc.page}")

        canvas.restoreState()

    return draw


# ── KPI cards ──────────────────────────────────────────────────────────────────

def _kpi_block(items: list[tuple[str, str, object, object]]) -> Table:
    """items = [(label, valor, bg_color, fg_color)]"""
    W   = A4[0] - 4*cm
    n   = len(items)
    cw  = W / n

    lbl_s = ParagraphStyle("KL", fontSize=7, fontName="Helvetica",
                            textColor=GRAY_LIGHT, alignment=1, leading=10)
    val_s = ParagraphStyle("KV", fontSize=22, fontName="Helvetica-Bold",
                            leading=26, alignment=1)

    row_labels = []
    row_values = []
    for lbl, val, bg, fg in items:
        row_labels.append(Paragraph(lbl, lbl_s))
        vs = ParagraphStyle(f"KV{lbl}", parent=val_s, textColor=fg)
        row_values.append(Paragraph(val, vs))

    t = Table([row_labels, row_values], colWidths=[cw]*n)
    cmds = [
        ("TOPPADDING",    (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("BOX",           (0,0), (-1,-1), 0, BG_DARK),
    ]
    for col, (_, _, bg, _) in enumerate(items):
        cmds.append(("BACKGROUND", (col,0), (col,-1), bg))
        if col < n-1:
            cmds.append(("LINEAFTER", (col,0), (col,-1), 1, BG_DARK))
    t.setStyle(TableStyle(cmds))
    return t


# ── Tabla de guias ─────────────────────────────────────────────────────────────

def _data_table(headers: list[str], col_w: list,
                rows_data: list[list], status_col: int,
                status_keys: list[str]) -> Table:

    data = [headers] + rows_data
    n_rows = len(rows_data)

    cell_s = ParagraphStyle("DC", fontSize=7.5, fontName="Helvetica",
                             textColor=WHITE, leading=10)
    hdr_s  = ParagraphStyle("DH", fontSize=8, fontName="Helvetica-Bold",
                             textColor=WHITE, alignment=1, leading=10)

    # Convertir headers a Paragraph
    data[0] = [Paragraph(h, hdr_s) for h in headers]

    cmds = [
        # Header
        ("BACKGROUND",    (0,0), (-1,0), BG_CARD),
        ("LINEBELOW",     (0,0), (-1,0), 2, YELLOW),
        ("TOPPADDING",    (0,0), (-1,0), 9),
        ("BOTTOMPADDING", (0,0), (-1,0), 9),
        # Datos
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1), (-1,-1), 7.5),
        ("TEXTCOLOR",     (0,1), (-1,-1), WHITE),
        ("TOPPADDING",    (0,1), (-1,-1), 6),
        ("BOTTOMPADDING", (0,1), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 7),
        ("RIGHTPADDING",  (0,0), (-1,-1), 7),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.4, BORDER),
        ("ALIGN",         (status_col,1), (status_col,-1), "CENTER"),
    ]

    for i, sk in enumerate(status_keys, start=1):
        bg_row = BG_ROW_A if i % 2 == 1 else BG_ROW_B
        _, s_bg, s_fg = _s(sk)
        # Fila base
        cmds.append(("BACKGROUND", (0,i), (-1,i), bg_row))
        # Badge de estado
        cmds.append(("BACKGROUND", (status_col,i), (status_col,i), s_bg))
        cmds.append(("TEXTCOLOR",  (status_col,i), (status_col,i), s_fg))
        cmds.append(("FONTNAME",   (status_col,i), (status_col,i), "Helvetica-Bold"))
        cmds.append(("FONTSIZE",   (status_col,i), (status_col,i), 7))

    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle(cmds))
    return t


# ── Servicio principal ─────────────────────────────────────────────────────────

class PdfService:

    def generate_range(self, rows: list[DailyReportRow],
                       fecha_inicio: date, fecha_fin: date,
                       output_path: Path, generated_at: datetime) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        period = f"{fecha_inicio.strftime('%d/%m/%Y')}  al  {fecha_fin.strftime('%d/%m/%Y')}"

        doc = SimpleDocTemplate(str(output_path), pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=3.8*cm, bottomMargin=2.5*cm)

        entregadas = sum(1 for r in rows if r.is_delivered)
        en_proceso = sum(1 for r in rows if not r.is_delivered and r.current_status in
                        ("en_ruta_entrega","en_transito","recogido"))
        novedades  = sum(1 for r in rows if r.current_status == "novedad")
        devueltas  = sum(1 for r in rows if r.current_status in ("devuelto","fallido"))

        story = [Spacer(1, 0.4*cm)]

        story.append(_kpi_block([
            ("Total Guias",   str(len(rows)),  BG_CARD,    BLUE_ACCENT),
            ("Entregadas",    str(entregadas), S_GREEN_BG, S_GREEN_FG),
            ("En Proceso",    str(en_proceso), S_BLUE_BG,  S_BLUE_FG),
            ("Novedades",     str(novedades),  S_ORANGE_BG,S_ORANGE_FG),
            ("Devueltas",     str(devueltas),  S_RED_BG,   S_RED_FG),
        ]))
        story.append(Spacer(1, 0.6*cm))

        W   = A4[0] - 4*cm
        hdrs = ["# Guia", "Cliente", "Asesor", "Estado", "Ult. Actualizacion", "Dias"]
        cws  = [W*.13, W*.24, W*.18, W*.20, W*.18, W*.07]

        rows_data   = []
        status_keys = []
        for r in rows:
            txt, _, _ = _s(r.current_status)
            rows_data.append([
                r.tracking_number,
                r.client_name or "—",
                r.advisor_name,
                txt,
                r.last_event_at.strftime("%d/%m/%Y %H:%M") if r.last_event_at else "—",
                str(int(r.days_without_movement or 0)),
            ])
            status_keys.append(r.current_status)

        story.append(_data_table(hdrs, cws, rows_data, 3, status_keys))
        story.append(Spacer(1, 0.4*cm))

        leg_s = ParagraphStyle("L", fontSize=7, fontName="Helvetica",
                               textColor=GRAY_MID, leading=10)
        story.append(Paragraph(
            "  Entregada      En Proceso / Despacho      Novedad      Devuelta / No Entregada      Pendiente",
            leg_s))

        cb = _canvas_cb("Informe de Guias TCC", period, generated_at)
        doc.build(story, onFirstPage=cb, onLaterPages=cb)
        return output_path

    def generate_daily(self, rows: list[DailyReportRow], output_path: Path,
                       cycle_label: str, report_date: date,
                       generated_at: datetime) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        period = f"{report_date.strftime('%d/%m/%Y')} -Ciclo {cycle_label}"
        doc = SimpleDocTemplate(str(output_path), pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=3.8*cm, bottomMargin=2.5*cm)

        entregadas = sum(1 for r in rows if r.is_delivered)
        alertas    = sum(1 for r in rows if r.is_alert)
        activas    = sum(1 for r in rows if not r.is_delivered)

        story = [Spacer(1, 0.4*cm)]
        story.append(_kpi_block([
            ("Total",      str(len(rows)),  BG_CARD,    BLUE_ACCENT),
            ("Entregadas", str(entregadas), S_GREEN_BG, S_GREEN_FG),
            ("Activas",    str(activas),    S_BLUE_BG,  S_BLUE_FG),
            ("Alertas",    str(alertas),    S_RED_BG,   S_RED_FG),
        ]))
        story.append(Spacer(1, 0.6*cm))

        W   = A4[0] - 4*cm
        hdrs = ["# Guia", "Cliente", "Asesor", "Estado TCC", "Ult. Actualizacion", "Dias"]
        cws  = [W*.13, W*.24, W*.18, W*.20, W*.18, W*.07]

        rows_data, status_keys = [], []
        for r in rows:
            txt, _, _ = _s(r.current_status)
            rows_data.append([
                r.tracking_number, r.client_name or "—", r.advisor_name, txt,
                r.last_event_at.strftime("%d/%m/%Y %H:%M") if r.last_event_at else "—",
                str(int(r.days_without_movement or 0)),
            ])
            status_keys.append(r.current_status)

        story.append(_data_table(hdrs, cws, rows_data, 3, status_keys))
        story.append(Spacer(1, 0.4*cm))

        leg_s = ParagraphStyle("L", fontSize=7, fontName="Helvetica",
                               textColor=GRAY_MID)
        story.append(Paragraph(
            "  Entregada      En Proceso / Despacho      Novedad      Devuelta / No Entregada",
            leg_s))

        cb = _canvas_cb("Reporte Diario de Guias TCC", period, generated_at)
        doc.build(story, onFirstPage=cb, onLaterPages=cb)
        return output_path

    def generate_weekly(self, rows: list[WeeklyReportRow], week_start: date,
                        week_end: date, output_path: Path,
                        generated_at: datetime) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        period = f"{week_start.strftime('%d/%m/%Y')}  al  {week_end.strftime('%d/%m/%Y')}"
        doc = SimpleDocTemplate(str(output_path), pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=3.8*cm, bottomMargin=2.5*cm)

        entregadas = sum(1 for r in rows if r.delivered_at)
        activas    = sum(1 for r in rows if r.still_active)
        con_alerta = sum(1 for r in rows if r.alerts_detected > 0)

        story = [Spacer(1, 0.4*cm)]
        story.append(_kpi_block([
            ("Total Semana",   str(len(rows)),  BG_CARD,    BLUE_ACCENT),
            ("Entregadas",     str(entregadas), S_GREEN_BG, S_GREEN_FG),
            ("Activas Cierre", str(activas),    S_BLUE_BG,  S_BLUE_FG),
            ("Con Alertas",    str(con_alerta), S_RED_BG,   S_RED_FG),
        ]))
        story.append(Spacer(1, 0.6*cm))

        W   = A4[0] - 4*cm
        hdrs = ["# Guia", "Cliente", "Asesor", "Ultimo Estado", "Fecha Entrega", "Activa", "Alertas"]
        cws  = [W*.13, W*.22, W*.17, W*.20, W*.14, W*.07, W*.07]

        rows_data, status_keys = [], []
        for r in rows:
            status_raw = r.last_status or "desconocido"
            # Normalizar el last_status a clave interna si viene como texto TCC
            from app.utils.status_normalizer import normalize_status
            sk = normalize_status(status_raw).value
            txt, _, _ = _s(sk)
            rows_data.append([
                r.tracking_number, r.client_name or "—", r.advisor_name, txt,
                r.delivered_at.strftime("%d/%m/%Y") if r.delivered_at else "—",
                "Si" if r.still_active else "No",
                str(r.alerts_detected) if r.alerts_detected else "—",
            ])
            status_keys.append(sk)

        story.append(_data_table(hdrs, cws, rows_data, 3, status_keys))
        story.append(Spacer(1, 0.4*cm))

        leg_s = ParagraphStyle("L", fontSize=7, fontName="Helvetica",
                               textColor=GRAY_MID)
        story.append(Paragraph(
            "  Entregada      Activa / En Proceso      Con Alerta",
            leg_s))

        cb = _canvas_cb("Consolidado Semanal de Guias TCC", period, generated_at)
        doc.build(story, onFirstPage=cb, onLaterPages=cb)
        return output_path
