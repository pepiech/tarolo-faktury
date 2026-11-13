from django.template.loader import render_to_string


def render_html(doc):
    return render_to_string("invoicing/pdf/invoice.html", {"doc": doc})


def render_pdf(doc):
    # Import až zde: WeasyPrint potřebuje systémové knihovny jen při generování PDF.
    from weasyprint import HTML

    return HTML(string=render_html(doc), base_url="about:blank").write_pdf()
