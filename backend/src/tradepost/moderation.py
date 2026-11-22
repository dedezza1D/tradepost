"""Moderation: prohibited listings, scams in chat and report triage.

Each check has two layers. Rules (word lists and patterns) always run, also on the live demo,
which has no model. When a model is available it classifies the text with a reason, and the
decision combines both as measured in ``eval/moderation_eval.py``. Nothing here punishes anyone
on its own: a risky listing waits for an admin instead of going live, a suspicious message gets
a warning next to it, and a report gets a suggested category and priority for the admin, who
decides.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.client import AI, AIUnavailable
from tradepost.chat.detection import OFF_PLATFORM_PAYMENT, warning_for

FEATURE_LISTING = "moderation_listing"
FEATURE_MESSAGE = "moderation_message"
FEATURE_REPORT = "moderation_report"

ListingCategory = Literal["weapons", "drugs", "counterfeit", "wildlife", "documents", "tobacco"]
LISTING_CATEGORIES: tuple[ListingCategory, ...] = (
    "weapons",
    "drugs",
    "counterfeit",
    "wildlife",
    "documents",
    "tobacco",
)


def _words(*terms: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")(?!\w)", re.IGNORECASE)


# Rules: words that name the prohibited thing, and words that show an allowed look-alike
# (a glue gun, a plush parrot). Deliberately plain: they are the floor when no model runs.
_LISTING_RULES: dict[ListingCategory, tuple[re.Pattern[str], re.Pattern[str] | None]] = {
    "weapons": (
        _words(
            r"pistolas?",
            r"rev[oó]lver(es)?",
            r"muni[cç][aã]o",
            r"espingardas?",
            r"carabinas?",
            r"fuzil",
            r"calibre",
            r"cartuchos?",
            r"silenciador",
            r"soco ingl[eê]s",
            r"taser",
            r"arma de (fogo|choque)",
            r"glock",
        ),
        _words(r"brinquedo", r"nerf", r"de cola", r"cola quente", r"de pintura", r"d'?[aá]gua"),
    ),
    "drugs": (
        _words(
            r"maconha",
            r"cannabis",
            r"coca[ií]na",
            r"crack",
            r"ecstasy",
            r"lsd",
            r"rivotril",
            r"clonazepam",
            r"ritalina",
            r"sibutramina",
            r"anabolizantes?",
            r"durateston",
            r"esteroides?",
            r"ozempic",
            r"sem receita",
            r"tarja preta",
            r"cbd",
        ),
        None,
    ),
    "counterfeit": (
        _words(
            r"r[eé]plica",
            r"primeira linha",
            r"1[ªa] linha",
            r"linha a",
            r"aaa",
            r"clone",
            r"igual ao original",
            r"paralel[oa]",
            r"n[aã]o [eé] original",
            r"contratipo",
        ),
        _words(r"miniatura", r"quadro", r"reprodu[cç][aã]o", r"decorativa"),
    ),
    "wildlife": (
        _words(
            r"papagaios?",
            r"araras?",
            r"mico",
            r"sagui",
            r"tucanos?",
            r"jabutis?",
            r"tartarugas?",
            r"can[aá]rio-da-terra",
            r"p[aá]ssaro de mato",
            r"sem anilha",
            r"ibama",
            r"macacos?",
        ),
        _words(r"pel[uú]cia", r"ra[cç][aã]o", r"gaiola", r"livro", r"guia"),
    ),
    "documents": (
        _words(
            r"cpfs?",
            r"cnh",
            r"carteira de motorista",
            r"diploma",
            r"atestado",
            r"conta (banc[aá]ria|laranja)",
            r"seguidores",
            r"login",
            r"dados completos",
        ),
        _words(r"porta-documentos", r"concursos?"),
    ),
    "tobacco": (
        _words(
            r"vapes?",
            r"pods? descart[aá]ve(l|is)",
            r"cigarros? eletr[oô]nicos?",
            r"puffs",
            r"elf ?bar",
            r"ignite",
            r"nicotina",
            r"narguil[eé]",
            r"ess[eê]ncia de tabaco",
        ),
        _words(r"parar de fumar", r"difusor"),
    ),
}


def listing_rules(title: str, description: str) -> ListingCategory | None:
    text = f"{title}\n{description}"
    for category, (hit, allowed) in _LISTING_RULES.items():
        if hit.search(text) and not (allowed and allowed.search(text)):
            return category
    return None


def message_rules(text: str) -> bool:
    """The existing chat warning: payment outside the platform."""
    return warning_for(text) == OFF_PLATFORM_PAYMENT


REPORT_PRIORITY = {"scam": 1, "prohibited": 2, "offensive": 2, "spam": 3, "other": 3}


def report_rules(reason: str, target: str) -> int:
    """Priority from the reporter's reason, raised when the rules see a prohibited listing."""
    priority = REPORT_PRIORITY.get(reason, 3)
    if reason == "prohibited" and listing_rules(target, ""):
        priority = 1
    return priority


# --- model ---------------------------------------------------------------------------------------

LISTING_PROMPT = (
    "Você modera anúncios de um marketplace brasileiro. Diga se o anúncio oferece algo proibido "
    "e em qual categoria:\n"
    "- weapons: armas de fogo, munição, peças de armas, silenciadores, soco inglês, armas de "
    "choque.\n"
    "- drugs: drogas ilícitas, medicamentos controlados ou de receita vendidos sem receita, "
    "anabolizantes.\n"
    "- counterfeit: produtos falsificados que usam marca alheia (réplica, primeira linha, "
    "clone), dinheiro falso.\n"
    "- wildlife: animais silvestres ou da fauna nativa sem documentação.\n"
    "- documents: documentos, diplomas, atestados, dados pessoais, contas bancárias ou de redes "
    "sociais.\n"
    "- tobacco: cigarros eletrônicos, pods, líquidos com nicotina, narguilé com tabaco.\n"
    "Não são proibidos: brinquedos (Nerf, armas de brinquedo), ferramentas (pistola de cola ou de "
    "pintura), facas de cozinha, réplicas decorativas e reproduções de arte sem marca, "
    "suplementos e vitaminas, acessórios para animais, livros. Na dúvida sobre algo comum, "
    "responda none.\n"
    "risk: high se é claramente proibido, medium se é provável, low se é improvável."
)
LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["none", *LISTING_CATEGORIES]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
    },
    "required": ["category", "risk", "reason"],
}

MESSAGE_PROMPT = (
    "Você protege compradores e vendedores no chat de um marketplace brasileiro, onde o "
    "pagamento é feito pelo checkout do site e o vendedor só recebe depois da entrega. Diga se "
    "a mensagem é uma tentativa de golpe: pedir pagamento fora do site (Pix, depósito, boleto ou "
    "link próprio), comprovante de pagamento que não passou pelo site, sinal ou taxa adiantada, "
    "links falsos, pedir senha, código ou dados de cartão, fingir ser o suporte, devolver valor "
    "pago a mais. Não é golpe: perguntar se aceita Pix ou cartão pelo checkout do site, dizer "
    "que pagou pelo site, combinar retirada, negociar preço, pedir fotos. Trocar telefone para "
    "combinar a retirada não é golpe por si."
)
MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scam": {"type": "boolean"},
        "kind": {
            "type": "string",
            "enum": [
                "none",
                "off_platform_payment",
                "fake_payment",
                "advance_fee",
                "phishing",
                "data_request",
                "impersonation",
            ],
        },
        "reason": {"type": "string"},
    },
    "required": ["scam", "kind", "reason"],
}

REPORT_PROMPT = (
    "Você faz a triagem de denúncias num marketplace brasileiro. Leia o motivo escolhido por "
    "quem denunciou, o comentário e o conteúdo denunciado. Classifique o que o conteúdo é de "
    "fato e dê a prioridade: 1 quando alguém pode perder dinheiro ou correr perigo (golpe, "
    "arma, droga, animal silvestre, documento falso, ameaça, ofensa racial); 2 quando precisa "
    "de revisão logo (falsificação, ofensa, anúncio enganoso, spam insistente); 3 quando é baixo "
    "risco ou não viola nada."
)
REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["scam", "prohibited", "offensive", "spam", "misleading", "no_violation"],
        },
        "priority": {"type": "integer", "enum": [1, 2, 3]},
        "reason": {"type": "string"},
    },
    "required": ["category", "priority", "reason"],
}


@dataclass(frozen=True)
class ListingVerdict:
    category: ListingCategory | None
    risk: str  # none, low, medium, high
    reason: str
    source: Literal["rules", "model"]


@dataclass(frozen=True)
class MessageVerdict:
    scam: bool
    kind: str
    reason: str
    source: Literal["rules", "model"]


@dataclass(frozen=True)
class ReportTriage:
    category: str
    priority: int
    reason: str
    source: Literal["rules", "model"]


async def _ask(
    db: AsyncSession, ai: AI | None, feature: str, prompt: str, text: str, schema: dict[str, Any]
) -> dict[str, Any] | None:
    if ai is None or not await ai.available(db):
        return None
    try:
        answer = await ai.complete_json(db, feature, prompt, text, schema, max_tokens=200)
    except AIUnavailable:
        return None
    return answer if isinstance(answer, dict) else None


async def classify_listing(
    db: AsyncSession, ai: AI | None, title: str, description: str
) -> ListingVerdict:
    """Held for review when the rules flag it, or a model sees medium or high risk.

    On the evaluation set this catches 98% of the prohibited listings with no false positive;
    the model alone caught half (it often names the right category but calls the risk low),
    and taking any category the model names held 19 innocent look-alikes."""
    by_rules = listing_rules(title, description)
    answer = await _ask(
        db,
        ai,
        FEATURE_LISTING,
        LISTING_PROMPT,
        f"Título: {title}\nDescrição: {description}",
        LISTING_SCHEMA,
    )
    reason = str(answer.get("reason", ""))[:300] if answer else ""
    if answer is not None:
        category = answer.get("category")
        risk = str(answer.get("risk", "low"))
        if category in LISTING_CATEGORIES and risk in ("medium", "high"):
            return ListingVerdict(category, risk, reason, "model")
    if by_rules:
        return ListingVerdict(by_rules, "high", reason, "rules")
    return ListingVerdict(None, "none", reason, "model" if answer else "rules")


def needs_review(verdict: ListingVerdict) -> bool:
    return verdict.category is not None and verdict.risk in ("medium", "high")


async def classify_message(db: AsyncSession, ai: AI | None, text: str) -> MessageVerdict:
    """With a model, the model decides: it beat the word rules on both counts (84% of scams
    caught against 40%, and no false alarm against 3, all honest questions about paying by Pix
    through the site). Without one, the rules are what there is."""
    answer = await _ask(db, ai, FEATURE_MESSAGE, MESSAGE_PROMPT, text, MESSAGE_SCHEMA)
    if answer is None:
        scam = message_rules(text)
        return MessageVerdict(scam, OFF_PLATFORM_PAYMENT if scam else "none", "", "rules")
    return MessageVerdict(
        bool(answer.get("scam")),
        str(answer.get("kind", "none")),
        str(answer.get("reason", ""))[:300],
        "model",
    )


async def triage_report(
    db: AsyncSession, ai: AI | None, reason: str, details: str, target: str
) -> ReportTriage:
    """Priority comes from the rules (85% of the urgent reports ranked first, against 31% for
    the model, which tends to call things low priority); a model adds what the content really
    is and why, which is what the admin reads."""
    priority = report_rules(reason, target)
    text = f"Motivo: {reason}\nComentário: {details}\nConteúdo denunciado: {target}"
    answer = await _ask(db, ai, FEATURE_REPORT, REPORT_PROMPT, text, REPORT_SCHEMA)
    if answer is None:
        return ReportTriage(reason, priority, "", "rules")
    return ReportTriage(
        str(answer.get("category", reason)), priority, str(answer.get("reason", ""))[:300], "model"
    )
