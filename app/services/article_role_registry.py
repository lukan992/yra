from dataclasses import dataclass


@dataclass(frozen=True)
class ArticleRoleRule:
    allowed_roles: tuple[str, ...]
    default_role: str
    can_cover_claims: tuple[str, ...] = ()
    cannot_cover_claims: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    user_visible_default: str = ""


class ArticleRoleRegistry:
    _GK_KEY = "гкрф"
    _RULES: dict[tuple[str, str], ArticleRoleRule] = {
        (_GK_KEY, "15"): ArticleRoleRule(
            allowed_roles=("damages_definition", "damages_recovery"),
            default_role="damages_definition",
            can_cover_claims=("damages",),
            user_visible_default="Статья определяет состав убытков и подтверждает, что их можно требовать при нарушении права.",
        ),
        (_GK_KEY, "307"): ArticleRoleRule(
            allowed_roles=("obligation_basis",),
            default_role="obligation_basis",
            user_visible_default="Статья закрепляет общее обязательственное отношение между сторонами и подходит как базовая норма по спору.",
        ),
        (_GK_KEY, "309"): ArticleRoleRule(
            allowed_roles=("obligation_basis", "performance_terms"),
            default_role="obligation_basis",
            user_visible_default="Статья требует надлежащего исполнения обязательства в соответствии с договором.",
        ),
        (_GK_KEY, "310"): ArticleRoleRule(
            allowed_roles=("performance_terms", "obligation_basis"),
            default_role="performance_terms",
            user_visible_default="Статья ограничивает односторонний отказ или изменение обязательства без законного основания.",
        ),
        (_GK_KEY, "314"): ArticleRoleRule(
            allowed_roles=("performance_terms",),
            default_role="performance_terms",
            user_visible_default="Статья регулирует срок исполнения обязательства и важна при споре о просрочке.",
        ),
        (_GK_KEY, "393"): ArticleRoleRule(
            allowed_roles=("damages_recovery", "liability_basis"),
            default_role="damages_recovery",
            can_cover_claims=("damages",),
            user_visible_default="Статья прямо позволяет требовать возмещение убытков за нарушение обязательства.",
        ),
        (_GK_KEY, "395"): ArticleRoleRule(
            allowed_roles=("monetary_obligation_interest",),
            default_role="monetary_obligation_interest",
            can_cover_claims=("interest",),
            cannot_cover_claims=("refund_principal", "damages"),
            conditions=("has_money_retention",),
            user_visible_default="Статья применяется к процентам за неправомерное удержание денежных средств, а не к возврату основной суммы.",
        ),
        (_GK_KEY, "405"): ArticleRoleRule(
            allowed_roles=("breach_or_delay",),
            default_role="breach_or_delay",
            conditions=("has_missed_deadline_or_delay",),
            user_visible_default="Статья относится к просрочке должника и подтверждает факт нарушения срока исполнения.",
        ),
        (_GK_KEY, "409"): ArticleRoleRule(
            allowed_roles=("termination_or_refusal", "refund_or_restitution"),
            default_role="termination_or_refusal",
            conditions=("has_settlement_in_lieu",),
            user_visible_default="Статья применяется только если стороны договорились об отступном.",
        ),
        (_GK_KEY, "420"): ArticleRoleRule(
            allowed_roles=("obligation_basis",),
            default_role="obligation_basis",
            user_visible_default="Статья определяет договор как основание возникновения обязательств между сторонами.",
        ),
        (_GK_KEY, "450"): ArticleRoleRule(
            allowed_roles=("termination_or_refusal",),
            default_role="termination_or_refusal",
            can_cover_claims=("termination/refusal",),
            user_visible_default="Статья регулирует основания изменения и расторжения договора.",
        ),
        (_GK_KEY, "450.1"): ArticleRoleRule(
            allowed_roles=("termination_or_refusal",),
            default_role="termination_or_refusal",
            can_cover_claims=("termination/refusal",),
            user_visible_default="Статья регулирует отказ от договора, если такой способ прекращения обязательства допускается законом или договором.",
        ),
        (_GK_KEY, "453"): ArticleRoleRule(
            allowed_roles=("refund_or_restitution", "termination_or_refusal"),
            default_role="refund_or_restitution",
            can_cover_claims=("refund_principal", "restitution"),
            conditions=("has_termination_or_refusal_basis",),
            user_visible_default="Статья описывает последствия изменения или расторжения договора и может поддерживать требование о возврате полученного только при наличии основания для расторжения или отказа.",
        ),
    }

    @classmethod
    def lookup(cls, act_name: str | None, article_number: str | None) -> ArticleRoleRule | None:
        normalized_act = cls.normalize_act_name(act_name)
        normalized_article = cls.normalize_article_number(article_number)
        if not normalized_act or not normalized_article:
            return None
        return cls._RULES.get((normalized_act, normalized_article))

    @staticmethod
    def normalize_act_name(act_name: str | None) -> str:
        if not isinstance(act_name, str):
            return ""
        return "".join(char for char in act_name.lower() if char.isalnum())

    @staticmethod
    def normalize_article_number(article_number: str | None) -> str:
        if not isinstance(article_number, str):
            return ""
        return article_number.strip().lower()
