import re


ARTICLE_REFERENCE_RE = re.compile(
    r"(?P<fragment>(?:пункт\s+\d+\s+)?стат(?:ья|ьи|ьями)\s+(?P<numbers>\d+(?:\.\d+)?(?:\s*(?:,|и)\s*\d+(?:\.\d+)?)*)?)",
    re.IGNORECASE,
)


class LawReferenceExtractor:
    def extract(self, article: dict) -> list[dict]:
        article_text = str(article.get("article_text") or "")
        act_name = str(article.get("act_name") or "")
        references: list[dict] = []
        for match in ARTICLE_REFERENCE_RE.finditer(article_text):
            numbers = re.findall(r"\d+(?:\.\d+)?", match.group("numbers") or "")
            for article_number in numbers:
                references.append(
                    {
                        "target_act_name": act_name,
                        "target_article_number": article_number,
                        "relation_type": "explicit_reference",
                        "source_fragment": match.group("fragment"),
                    }
                )
        return references
