from app.repositories.law_repository import LawRepository
from app.services.article_semantic_analyzer import ArticleSemanticAnalyzer
from app.services.claim_entailment_checker import ClaimEntailmentChecker
from app.services.law_reference_extractor import LawReferenceExtractor


class LawGraphExpander:
    def __init__(
        self,
        law_repository: LawRepository,
        reference_extractor: LawReferenceExtractor,
        semantic_analyzer: ArticleSemanticAnalyzer | None = None,
        entailment_checker: ClaimEntailmentChecker | None = None,
    ) -> None:
        self.law_repository = law_repository
        self.reference_extractor = reference_extractor
        self.semantic_analyzer = semantic_analyzer or ArticleSemanticAnalyzer()
        self.entailment_checker = entailment_checker or ClaimEntailmentChecker()
        self.last_trace: dict[str, list[dict]] = {"added_articles": []}

    def expand(
        self,
        reranked_articles: list[dict],
        facts: dict | None = None,
        user_text: str = "",
        normalized_claims: list[str] | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict]:
        result: list[dict] = []
        seen_ids: set[str] = set()
        added_articles: list[dict] = []
        rejected_articles: list[dict] = []
        for article in reranked_articles:
            article_id = str(article.get("id") or "")
            if article_id and article_id not in seen_ids:
                seen_ids.add(article_id)
                result.append(article)

            references = self.reference_extractor.extract(article)
            if article_id:
                self.law_repository.replace_references(article_id, references)
            for reference in references:
                target = self.law_repository.get_active_article(
                    act_name=str(reference.get("target_act_name") or ""),
                    article_number=str(reference.get("target_article_number") or ""),
                )
                if not target:
                    continue
                target_id = str(target["id"])
                if target_id in seen_ids:
                    continue
                target = dict(target)
                seen_ids.add(target_id)
                target["relation_type"] = "explicit_reference"
                target["source_fragment"] = reference.get("source_fragment")
                if normalized_claims:
                    target["semantic_analysis"] = self.semantic_analyzer.analyze(target, request_id=request_id, run_id=run_id)
                    target["legal_effects"] = target["semantic_analysis"].get("legal_effects", [])
                    coverage = self.entailment_checker.build_coverage(
                        normalized_claims,
                        [target],
                        facts or {},
                        user_text,
                        request_id=request_id,
                        run_id=run_id,
                    )
                    target["coverage"] = target.get("coverage", [])
                    target["coverage_type"] = target.get("coverage_type")
                    allowed = any(
                        entry.get("counts_as_covered") or entry.get("coverage_type") == "supporting"
                        for entry in target.get("coverage", [])
                        if isinstance(entry, dict)
                    )
                    if not allowed:
                        rejected_articles.append(
                            {
                                "id": target_id,
                                "act_name": target.get("act_name"),
                                "article_number": target.get("article_number"),
                                "title": target.get("article_title"),
                                "relation_type": "explicit_reference",
                                "source_article_id": article_id,
                                "reason": "referenced_article_failed_entailment",
                                "coverage": coverage.get("claims"),
                            }
                        )
                        continue
                result.append(target)
                added_articles.append(
                    {
                        "id": target_id,
                        "act_name": target.get("act_name"),
                        "article_number": target.get("article_number"),
                        "title": target.get("article_title"),
                        "relation_type": "explicit_reference",
                        "source_article_id": article_id,
                        "source_article_number": article.get("article_number"),
                        "reason": reference.get("source_fragment"),
                    }
                )
        self.last_trace = {"added_articles": added_articles, "rejected_articles": rejected_articles}
        return result
