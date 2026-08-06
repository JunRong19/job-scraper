import io
import re
import logging
from datetime import datetime, date
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.units import inch
from reportlab.lib import colors
from models import Resume

logging.basicConfig(level=logging.INFO)


def _markdown_bold_to_xml(text: str) -> str:
    """
    Escapes XML special characters, then converts **bold** markdown (used by
    the LLM to flag key metrics/impact phrases) into ReportLab <b> tags.
    """
    if not text:
        return text
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return text


_DATE_FORMATS = ("%b %Y", "%B %Y", "%m/%Y", "%Y-%m", "%m-%Y")


def _parse_date_token(token: str):
    """
    Best-effort parse of a resume date string into a (year, month) tuple.
    Supports bare years ("2025"), "Mon YYYY", "Month YYYY", "MM/YYYY", "YYYY-MM".
    Returns None if it can't confidently be parsed.
    """
    if not token:
        return None
    token = token.strip()
    if token.upper() in ("NA", "PRESENT", "CURRENT", "ONGOING"):
        today = date.today()
        return (today.year, today.month)
    if re.fullmatch(r"\d{4}", token):
        # Year-only granularity: treat as January so whole-year spans compute cleanly.
        return (int(token), 1)
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(token, fmt)
            return (dt.year, dt.month)
        except ValueError:
            continue
    return None


def _format_duration(start_date: str, end_date: str) -> str:
    """
    Computes a human-readable duration like "1 Year" or "1 Year 6 Months" from
    start/end date strings. Returns "" if the dates can't be parsed.
    """
    start = _parse_date_token(start_date)
    end = _parse_date_token(end_date)
    if not start or not end:
        return ""

    total_months = (end[0] - start[0]) * 12 + (end[1] - start[1])
    if total_months <= 0:
        total_months = 1

    if total_months < 12:
        return f"{total_months} Month{'s' if total_months != 1 else ''}"

    years, months = divmod(total_months, 12)
    duration = f"{years} Year{'s' if years != 1 else ''}"
    if months:
        duration += f" {months} Month{'s' if months != 1 else ''}"
    return duration


def create_resume_pdf(resume_data: Resume) -> bytes:
    """
    Generates an ATS-friendly PDF resume with improved design from the provided Resume data object.
    Returns the PDF content as bytes.
    """
    buffer = io.BytesIO()

    # Document setup with slightly wider margins for better readability
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.6*inch,
        rightMargin=0.6*inch,
        topMargin=0.6*inch,
        bottomMargin=0.6*inch
    )

    # Create custom styles
    styles = getSampleStyleSheet()

    # Define a modern color palette
    primary_color = colors.HexColor('#1976D2')  # Modern blue
    secondary_color = colors.HexColor('#455A64')  # Dark blue-gray
    text_color = colors.HexColor('#212121')  # Near black
    light_text = colors.HexColor('#757575')  # Medium gray

    # Create custom styles using ReportLab's built-in fonts
    style_name = ParagraphStyle(
        name='Name',
        parent=styles['Heading1'],
        fontSize=24,
        alignment=TA_CENTER,
        spaceAfter=2,
        fontName='Times-Bold',
        textColor=text_color,
    )

    style_tagline = ParagraphStyle(
        name='Tagline',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=10,
        leading=13,
        spaceAfter=6,
        fontName='Helvetica',
        textColor=secondary_color,
    )

    style_section_heading = ParagraphStyle(
        name='SectionHeading',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=8,
        spaceAfter=2,
        fontName='Helvetica-Bold',
        textColor=primary_color,
        alignment=TA_LEFT,
    )

    style_normal = ParagraphStyle(
        name='Normal',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        fontName='Helvetica',
        textColor=text_color,
    )

    style_skill_category = ParagraphStyle(
        name='SkillCategory',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        fontName='Helvetica',
        textColor=text_color,
        spaceAfter=3,
    )

    style_exp_company = ParagraphStyle(
        name='ExpCompany',
        parent=styles['Normal'],
        fontSize=11,
        fontName='Helvetica-Bold',
        textColor=text_color,
        spaceAfter=1,
    )

    style_exp_role = ParagraphStyle(
        name='ExpRole',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica-Oblique',
        textColor=text_color,
        spaceAfter=2,
    )

    style_exp_location = ParagraphStyle(
        name='ExpLocation',
        parent=styles['Normal'],
        fontSize=9.5,
        fontName='Helvetica',
        textColor=secondary_color,
    )

    style_job_title = ParagraphStyle(
        name='JobTitle',
        parent=styles['Normal'],
        fontSize=12,
        spaceAfter=4,
        fontName='Helvetica-Bold',
        textColor=primary_color,
    )

    style_dates = ParagraphStyle(
        name='Dates',
        parent=styles['Normal'],
        fontSize=9,
        alignment=TA_RIGHT,
        fontName='Helvetica-Oblique',
        textColor=light_text,
    )

    style_bullet = ParagraphStyle(
        name='Bullet',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        leftIndent=15,
        bulletIndent=0,
        fontName='Helvetica',
        textColor=text_color,
        spaceAfter=4,
    )

    style_tech = ParagraphStyle(
        name='Technologies',
        parent=styles['Normal'],
        fontSize=9,
        fontName='Helvetica-Oblique',
        textColor=light_text,
        spaceAfter=4,
    )

    story =[]

    # --- Header ---
    if resume_data.name:
        story.append(Paragraph(resume_data.name, style_name))

    # --- Centered tagline: title | email | phone | location | links ---
    def format_link(url, label):
        clean_url = url if url.startswith('http') else f"https://{url}"
        clean_url = clean_url.replace('&', '&amp;')
        return f'<u><a href="{clean_url}"><font color="#1976D2">{label}</font></a></u>'

    tagline_parts = []
    if resume_data.title and resume_data.title != "NA":
        tagline_parts.append(resume_data.title)
    if resume_data.phone and resume_data.phone != "NA":
        tagline_parts.append(resume_data.phone)
    if resume_data.email and resume_data.email != "NA":
        tagline_parts.append(resume_data.email)
    if resume_data.location and resume_data.location != "NA":
        tagline_parts.append(resume_data.location)

    if resume_data.links:
        if resume_data.links.linkedin and resume_data.links.linkedin != "NA":
            tagline_parts.append(format_link(resume_data.links.linkedin, "LinkedIn"))
        if resume_data.links.github and resume_data.links.github != "NA":
            tagline_parts.append(format_link(resume_data.links.github, "GitHub"))
        if resume_data.links.portfolio and resume_data.links.portfolio != "NA":
            tagline_parts.append(format_link(resume_data.links.portfolio, "Portfolio"))

    if tagline_parts:
        story.append(Paragraph(" | ".join(tagline_parts), style_tagline))

    # --- Skills ---
    skill_categories = [
        cat for cat in (resume_data.skill_categories or [])
        if cat.label and cat.label != "NA" and cat.skills
    ]

    if skill_categories:
        story.append(Paragraph("SKILLS", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

        for cat in skill_categories:
            skills_str = ", ".join(s for s in cat.skills if s != "NA")
            if skills_str:
                label = cat.label.rstrip(':').strip()
                story.append(Paragraph(f"<b>{label}:</b> {skills_str}", style_skill_category))
        story.append(Spacer(1, 0.05*inch))

    elif resume_data.skills and resume_data.skills != ["NA"]:
        # Fallback: no categorized data available, use the old 3-column grid.
        seen_skills = set()
        skills_list = []
        for s in resume_data.skills:
            if s == "NA":
                continue
            key = s.strip().lower()
            if key in seen_skills:
                continue
            seen_skills.add(key)
            skills_list.append(s)

        if skills_list:
            story.append(Paragraph("SKILLS", style_section_heading))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

            num_columns = 3  # We'll use a 3-column layout

            # Prepare data for the table
            table_data =[]
            num_skills = len(skills_list)
            # Calculate number of rows needed (ceiling division)
            rows = (num_skills + num_columns - 1) // num_columns

            for i in range(rows):
                row_items =[]
                for j in range(num_columns):
                    skill_index = i * num_columns + j # This fills row by row
                    if skill_index < num_skills:
                        skill_text = f"• {skills_list[skill_index]}" # Add a bullet point
                        row_items.append(Paragraph(skill_text, style_normal))
                    else:
                        row_items.append(Paragraph("", style_normal)) # Empty cell for padding
                table_data.append(row_items)

            if table_data:
                # Calculate available width for the table
                page_width_available = letter[0] - doc.leftMargin - doc.rightMargin
                col_width = page_width_available / num_columns

                # Define column widths for the table
                colWidths = [col_width] * num_columns

                skills_table = Table(table_data, colWidths=colWidths)
                skills_table.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'TOP'),          # Align content to the top of cells
                    ('LEFTPADDING', (0,0), (0,-1), 10),         # No left padding for cells
                    ('RIGHTPADDING', (0,0), (-1,-1), 6),        # Padding between columns (applied to right of each cell)
                    ('BOTTOMPADDING', (0,0), (-1,-1), 3),       # Padding below each row
                ]))
                story.append(skills_table)
                story.append(Spacer(1, 0.05*inch)) # Add some space after the skills section

    def render_description(description: str):
        """Renders a description as bullet points, honoring **bold** markdown spans."""
        if '\n' in description:
            bullets = description.split('\n')
            for bullet in bullets:
                if not bullet.strip():
                    continue
                bullet_text = bullet.strip()
                if bullet_text.startswith('-'):
                    bullet_text = bullet_text[1:].strip()
                elif bullet_text.startswith('•'):
                    bullet_text = bullet_text[1:].strip()
                story.append(Paragraph(f"• {_markdown_bold_to_xml(bullet_text)}", style_bullet))
        else:
            text = description.strip()

            text = text.replace("e.g.", "TEMP_EG")
            text = text.replace("i.e.", "TEMP_IE")
            text = text.replace("etc.", "TEMP_ETC")
            text = text.replace("vs.", "TEMP_VS")
            text = text.replace("Mr.", "TEMP_MR")
            text = text.replace("Mrs.", "TEMP_MRS")
            text = text.replace("Ms.", "TEMP_MS")
            text = text.replace("Dr.", "TEMP_DR")
            text = text.replace("St.", "TEMP_ST")
            text = text.replace("Ph.D.", "TEMP_PHD")
            text = text.replace("U.S.", "TEMP_US")
            text = text.replace("U.K.", "TEMP_UK")

            sentences = text.split('. ')

            for i, sentence in enumerate(sentences):
                if not sentence:
                    continue
                sentence = sentence.replace("TEMP_EG", "e.g.")
                sentence = sentence.replace("TEMP_IE", "i.e.")
                sentence = sentence.replace("TEMP_ETC", "etc.")
                sentence = sentence.replace("TEMP_VS", "vs.")
                sentence = sentence.replace("TEMP_MR", "Mr.")
                sentence = sentence.replace("TEMP_MRS", "Mrs.")
                sentence = sentence.replace("TEMP_MS", "Ms.")
                sentence = sentence.replace("TEMP_DR", "Dr.")
                sentence = sentence.replace("TEMP_ST", "St.")
                sentence = sentence.replace("TEMP_PHD", "Ph.D.")
                sentence = sentence.replace("TEMP_US", "U.S.")
                sentence = sentence.replace("TEMP_UK", "U.K.")

                if i < len(sentences) - 1 or not sentence[-1] in ['.', '!', '?']:
                    sentence = sentence + '.'

                story.append(Paragraph(f"• {_markdown_bold_to_xml(sentence.strip())}", style_bullet))

    # --- Experience ---
    if resume_data.experience:
        story.append(Paragraph("PROFESSIONAL EXPERIENCE", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

        for exp in resume_data.experience:
            # --- Line 1: Company name (bold) ---
            if exp.company and exp.company != "NA":
                story.append(Paragraph(exp.company, style_exp_company))

            # --- Line 2: Job title (italic), with computed duration if dates allow it ---
            # Skip if the title already ends with a "(...)" duration (older resume data
            # sometimes has this baked into the title from the original resume text).
            role_text = exp.job_title if exp.job_title != "NA" else ""
            duration = _format_duration(exp.start_date, exp.end_date)
            if role_text and duration and not re.search(r'\([^)]*\)\s*$', role_text):
                role_text = f"{role_text} ({duration})"
            if role_text:
                story.append(Paragraph(role_text, style_exp_role))

            # --- Line 3: Location (left) | Dates (right) ---
            location_text = exp.location if exp.location and exp.location != "NA" else ""

            dates = ""
            if exp.start_date and exp.start_date != "NA" and exp.end_date and exp.end_date != "NA":
                dates = f"{exp.start_date} - {exp.end_date}"
            elif exp.start_date and exp.start_date != "NA":
                dates = f"{exp.start_date} - Present"

            if location_text or dates:
                data = [[Paragraph(location_text, style_exp_location), Paragraph(dates, style_dates)]]
                tbl = Table(data, colWidths=[4.636*inch, 2.5*inch])
                tbl.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                    ('LEFTPADDING', (0, 0), (0, -1), 0),
                ]))
                story.append(tbl)

            story.append(Spacer(1, 0.08*inch))

            if exp.description and exp.description != "NA":
                render_description(exp.description)

            story.append(Spacer(1, 0.08*inch))

    # --- Projects ---
    if resume_data.projects:
        story.append(Paragraph("PROJECTS", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

        for proj in resume_data.projects:
            if proj.name and proj.name != "NA":
                story.append(Paragraph(f"<b>{proj.name}</b>", style_job_title))

            if proj.technologies and proj.technologies != ["NA"]:
                tech_list =[t for t in proj.technologies if t != "NA"]
                if tech_list:
                    tech_text = f"<i>Technologies:</i> {', '.join(tech_list)}"
                    story.append(Paragraph(tech_text, style_tech))

            if proj.description and proj.description != "NA":
                render_description(proj.description)

            story.append(Spacer(1, 0.08*inch))

    # --- Education (kept last, after Skills/Experience/Projects) ---
    if resume_data.education:
        story.append(Paragraph("EDUCATION", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

        for edu in resume_data.education:
            # Degree info
            degree_info = f"<b>{edu.degree}</b>" if edu.degree != "NA" else ""
            if edu.field_of_study and edu.field_of_study != "NA":
                degree_info += f", {edu.field_of_study}"

            # Year info
            years = ""
            if edu.start_year and edu.start_year != "NA" and edu.end_year and edu.end_year != "NA":
                years = f"{edu.start_year} - {edu.end_year}"
            elif edu.start_year and edu.start_year != "NA":
                years = f"Started {edu.start_year}"
            elif edu.end_year and edu.end_year != "NA":
                years = f"Graduated {edu.end_year}"

            # Create two-column layout
            data = [[Paragraph(degree_info, style_normal), Paragraph(years, style_dates)]]
            tbl = Table(data, colWidths=[5.15*inch, 2*inch])
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (0, -1), 0),
            ]))
            story.append(tbl)

            if edu.institution and edu.institution != "NA":
                story.append(Paragraph(edu.institution, style_normal))
            story.append(Spacer(1, 0.08*inch))

    # --- Certifications ---
    if resume_data.certifications:
        story.append(Paragraph("CERTIFICATIONS", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))

        for cert in resume_data.certifications:
            if cert.name == "NA" and cert.issuer == "NA":
                continue

            cert_name = f"<b>{cert.name}</b>" if cert.name != "NA" else ""

            # Right aligned year if available
            year_text = ""
            if cert.year and cert.year != "NA":
                year_text = cert.year

            # Create a table for certification info
            data = [[Paragraph(cert_name, style_normal), Paragraph(year_text, style_dates)]]
            tbl = Table(data, colWidths=[5.3*inch, 2*inch])
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(tbl)

            if cert.issuer and cert.issuer != "NA":
                story.append(Paragraph(cert.issuer, style_normal))

            story.append(Spacer(1, 0.06*inch))

    # --- Languages ---
    if resume_data.languages and resume_data.languages != ["NA"]:
        lang_list =[l for l in resume_data.languages if l != "NA"]
        if lang_list:
            story.append(Paragraph("LANGUAGES", style_section_heading))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#2C3E50'), spaceBefore=0, spaceAfter=5))
            story.append(Paragraph(", ".join(lang_list), style_normal))

    try:
        doc.build(story)
        logging.info("PDF generated successfully.")
    except Exception as e:
        logging.error(f"Error building PDF: {e}")
        raise  # Re-raise the exception

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
