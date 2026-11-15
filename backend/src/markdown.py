import markdown
import nh3
from markupsafe import Markup


# 1. Define the filter function
def render_markdown(text: str) -> Markup:
    if not text:
        return Markup("")
    # Convert Markdown to HTML (with fenced code block support)
    raw_html = markdown.markdown(text, extensions=["fenced_code", "tables"])
    # Sanitize HTML if the content comes from untrusted users
    clean_html = nh3.clean(raw_html)
    return Markup(clean_html)