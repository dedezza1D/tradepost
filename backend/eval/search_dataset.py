"""Search evaluation set: a catalog of listings in Brazilian Portuguese and queries with judged
relevance.

The catalog is generated from templates with a fixed seed, so every run sees the same listings.
Each listing carries structured attributes (product type, brand, size...), and each query states
what it is looking for in the same terms. Relevance is computed from those attributes instead of
being labelled by hand:

- grade 2: the right kind of product and every attribute the query asks for;
- grade 1: the right kind of product, some attribute differs;
- grade 0: anything else.

Queries are written the way people type: synonyms ("magrela", "refrigerador"), English words
("mountain bike", "air fryer"), typos, vague needs ("algo pra trilha"). Half of them (by a hash
of the text) form the dev split used to tune the search, the other half the test split the
README reports, so the published number was not tuned on.
"""

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

SEED = 20261119


@dataclass(frozen=True)
class Item:
    key: str
    kind: str
    category: str
    title: str
    description: str
    price_cents: int
    attrs: dict[str, str]


@dataclass(frozen=True)
class Query:
    text: str
    kind: str | tuple[str, ...]
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def split(self) -> str:
        h = int(hashlib.sha1(self.text.encode(), usedforsecurity=False).hexdigest(), 16)
        return "dev" if h % 2 == 0 else "test"

    def grade(self, item: Item) -> int:
        kinds = (self.kind,) if isinstance(self.kind, str) else self.kind
        if item.kind not in kinds:
            return 0
        if all(item.attrs.get(k) == v for k, v in self.attrs.items()):
            return 2
        return 1


def _pick(rng: random.Random, options: list[Any]) -> Any:
    return options[rng.randrange(len(options))]


CONDITION_PHRASES = [
    "Pouco uso, em ótimo estado.",
    "Funcionando perfeitamente, sem detalhes.",
    "Tem marcas de uso, mas tudo funciona.",
    "Novo, ainda na caixa.",
    "Conservado, sempre guardado.",
    "Vendo porque comprei outro.",
]
DELIVERY_PHRASES = [
    "Entrego no centro.",
    "Retirada no bairro ou envio pelos Correios.",
    "Aceito propostas.",
    "Envio para todo o Brasil.",
    "",
]


def _bikes(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(14):
        brand = _pick(rng, ["Caloi", "Specialized", "Oggi", "Sense", "Trek", "Soul"])
        size = _pick(rng, ["26", "29", "700"])
        style = "speed" if size == "700" else _pick(rng, ["mountain", "urbana"])
        gears = _pick(rng, ["18", "21", "24"])
        title = _pick(
            rng,
            [
                f"Bicicleta {brand} aro {size}",
                f"Bike {brand} aro {size} {gears} marchas",
                f"{'Mountain bike' if style == 'mountain' else 'Bicicleta ' + style} {brand} aro {size}",
                f"Bicicleta {style} aro {size} {brand}",
            ],
        )
        extra = {
            "mountain": "Ideal para trilha e estrada de terra, suspensão dianteira.",
            "urbana": "Ótima para ir ao trabalho, com bagageiro e paralama.",
            "speed": "Quadro leve de alumínio, pneus finos, para asfalto.",
        }[style]
        desc = f"{extra} Freio a disco, {gears} marchas. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "bicicleta",
                "category": "bikes",
                "title": title,
                "description": desc,
                "price": rng.randrange(600, 6000),
                "attrs": {"aro": size, "estilo": style, "marca": brand.lower()},
            }
        )
    return out


def _phones(rng: random.Random) -> list[dict[str, Any]]:
    models = [
        ("apple", "iPhone 11"),
        ("apple", "iPhone 13"),
        ("apple", "iPhone 14 Pro"),
        ("samsung", "Galaxy S21"),
        ("samsung", "Galaxy A54"),
        ("motorola", "Moto G84"),
        ("xiaomi", "Redmi Note 12"),
    ]
    out = []
    for _ in range(14):
        brand, model = _pick(rng, models)
        storage = _pick(rng, ["64", "128", "256"])
        title = _pick(
            rng,
            [
                f"{model} {storage}GB",
                f"Celular {model} {storage} GB",
                f"Smartphone {model} {storage}gb desbloqueado",
            ],
        )
        desc = f"Bateria boa, tela sem riscos. Acompanha carregador. {_pick(rng, CONDITION_PHRASES)} {_pick(rng, DELIVERY_PHRASES)}"
        out.append(
            {
                "kind": "celular",
                "category": "phones",
                "title": title,
                "description": desc,
                "price": rng.randrange(700, 7000),
                "attrs": {"marca": brand, "armazenamento": storage},
            }
        )
    return out


def _laptops(rng: random.Random) -> list[dict[str, Any]]:
    models = [
        ("dell", "Dell Inspiron 15"),
        ("lenovo", "Lenovo IdeaPad 3"),
        ("apple", "MacBook Air M1"),
        ("asus", "Asus Vivobook"),
        ("acer", "Acer Aspire 5"),
    ]
    out = []
    for _ in range(12):
        brand, model = _pick(rng, models)
        ram = _pick(rng, ["8", "16"])
        title = _pick(
            rng,
            [
                f"Notebook {model} {ram}GB RAM",
                f"{model} {ram}gb SSD 256",
                f"Laptop {model} i5 {ram}GB",
            ],
        )
        desc = f"SSD rápido, bateria dura bastante. Bom para estudo e trabalho. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "notebook",
                "category": "computers",
                "title": title,
                "description": desc,
                "price": rng.randrange(1500, 7000),
                "attrs": {"marca": brand, "ram": ram},
            }
        )
    return out


def _fridges(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        brand = _pick(rng, ["Brastemp", "Consul", "Electrolux", "Panasonic"])
        doors = _pick(rng, ["duplex", "uma porta"])
        liters = _pick(rng, ["300", "375", "450"])
        title = _pick(
            rng,
            [
                f"Geladeira {brand} {doors} {liters} litros",
                f"Geladeira frost free {brand} {liters}L",
                f"Refrigerador {brand} {doors} frost free",
            ],
        )
        desc = f"Gela muito bem, {'duas portas' if doors == 'duplex' else 'uma porta'}, frost free. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "geladeira",
                "category": "appliances",
                "title": title,
                "description": desc,
                "price": rng.randrange(800, 4000),
                "attrs": {"portas": doors, "marca": brand.lower()},
            }
        )
    return out


def _sofas(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        seats = _pick(rng, ["2", "3"])
        color = _pick(rng, ["cinza", "bege", "azul", "marrom"])
        recl = _pick(rng, [True, False])
        title = _pick(
            rng,
            [
                f"Sofá {seats} lugares {color}",
                f"Sofá {'retrátil e reclinável' if recl else 'de canto'} {color}",
                f"Sofa {seats} lugares {'retratil' if recl else ''}".strip(),
            ],
        )
        desc = f"Tecido suede {color}, {seats} lugares, {'retrátil e reclinável' if recl else 'estrutura firme'}. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "sofa",
                "category": "furniture",
                "title": title,
                "description": desc,
                "price": rng.randrange(400, 3500),
                "attrs": {"lugares": seats, "cor": color, "retratil": str(recl).lower()},
            }
        )
    return out


def _tables(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(8):
        seats = _pick(rng, ["4", "6"])
        material = _pick(rng, ["madeira", "vidro"])
        title = _pick(
            rng,
            [
                f"Mesa de jantar {seats} lugares {material}",
                f"Conjunto de mesa com {seats} cadeiras",
                f"Mesa {material} {seats} cadeiras",
            ],
        )
        desc = f"Tampo de {material}, acompanha {seats} cadeiras estofadas. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "mesa",
                "category": "furniture",
                "title": title,
                "description": desc,
                "price": rng.randrange(300, 2500),
                "attrs": {"lugares": seats, "material": material},
            }
        )
    return out


def _sneakers(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(12):
        brand = _pick(rng, ["Nike", "Adidas", "Olympikus", "Mizuno", "Asics"])
        size = _pick(rng, ["37", "39", "40", "42"])
        use = _pick(rng, ["corrida", "casual"])
        title = _pick(
            rng,
            [
                f"Tênis {brand} {use} número {size}",
                f"Tenis {brand} tamanho {size}",
                f"Tênis de {use} {brand} {size}",
            ],
        )
        desc = f"{'Amortecimento ótimo para correr' if use == 'corrida' else 'Confortável para o dia a dia'}. Número {size}. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "tenis",
                "category": "shoes",
                "title": title,
                "description": desc,
                "price": rng.randrange(120, 900),
                "attrs": {"marca": brand.lower(), "numero": size, "uso": use},
            }
        )
    return out


def _guitars(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        kind = _pick(rng, ["violao", "guitarra"])
        brand = _pick(rng, ["Yamaha", "Giannini", "Tagima", "Fender"])
        if kind == "violao":
            strings = _pick(rng, ["nylon", "aço"])
            title = _pick(
                rng,
                [
                    f"Violão {brand} cordas de {strings}",
                    f"Violão elétrico {brand}",
                    f"Violao {brand} {strings}",
                ],
            )
            desc = f"Som encorpado, cordas de {strings}, acompanha capa. {_pick(rng, CONDITION_PHRASES)}"
            attrs = {"marca": brand.lower(), "cordas": strings}
        else:
            title = _pick(
                rng,
                [
                    f"Guitarra {brand} Stratocaster",
                    f"Guitarra elétrica {brand}",
                    f"Guitarra {brand} com amplificador",
                ],
            )
            desc = f"Captadores originais, braço regulado. {_pick(rng, CONDITION_PHRASES)}"
            attrs = {"marca": brand.lower()}
        out.append(
            {
                "kind": kind,
                "category": "music",
                "title": title,
                "description": desc,
                "price": rng.randrange(300, 4000),
                "attrs": attrs,
            }
        )
    return out


def _books(rng: random.Random) -> list[dict[str, Any]]:
    books = [
        ("machado de assis", "Dom Casmurro"),
        ("machado de assis", "Memórias Póstumas de Brás Cubas"),
        ("aluísio azevedo", "O Cortiço"),
        ("clarice lispector", "A Hora da Estrela"),
        ("jorge amado", "Capitães da Areia"),
        ("graciliano ramos", "Vidas Secas"),
    ]
    out = []
    for author, name in books:
        for edition in ("edição de bolso", "capa dura"):
            title = f"Livro {name} - {author.title()}"
            desc = f"Clássico da literatura brasileira, {edition}. {_pick(rng, CONDITION_PHRASES)}"
            out.append(
                {
                    "kind": "livro",
                    "category": "books",
                    "title": title,
                    "description": desc,
                    "price": rng.randrange(15, 80),
                    "attrs": {"autor": author, "obra": name.lower()},
                }
            )
    return out


def _consoles(rng: random.Random) -> list[dict[str, Any]]:
    models = [
        ("sony", "PlayStation 5"),
        ("sony", "PlayStation 4 Slim"),
        ("microsoft", "Xbox Series S"),
        ("microsoft", "Xbox One"),
        ("nintendo", "Nintendo Switch"),
    ]
    out = []
    for _ in range(10):
        brand, model = _pick(rng, models)
        title = _pick(
            rng, [f"{model} com 2 controles", f"Console {model}", f"Video game {model} + jogos"]
        )
        desc = f"Acompanha cabos e controle original. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "console",
                "category": "games",
                "title": title,
                "description": desc,
                "price": rng.randrange(900, 4500),
                "attrs": {"marca": brand},
            }
        )
    return out


def _cameras(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(8):
        brand, model = _pick(
            rng,
            [
                ("canon", "Canon EOS Rebel T6i"),
                ("nikon", "Nikon D3500"),
                ("sony", "Sony Alpha a6000"),
            ],
        )
        title = _pick(
            rng, [f"Câmera {model} com lente 18-55", f"Camera fotográfica {model}", f"{model} kit"]
        )
        desc = f"Câmera digital, sensor limpo, acompanha bateria e carregador. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "camera",
                "category": "electronics",
                "title": title,
                "description": desc,
                "price": rng.randrange(1200, 4500),
                "attrs": {"marca": brand},
            }
        )
    return out


def _headphones(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        brand = _pick(rng, ["JBL", "Apple", "Sony", "Xiaomi"])
        wireless = _pick(rng, ["bluetooth", "com fio"])
        title = _pick(
            rng,
            [
                f"Fone de ouvido {brand} {wireless}",
                f"Fone {brand} {'sem fio' if wireless == 'bluetooth' else 'com fio'}",
                f"{'AirPods' if brand == 'Apple' else 'Headphone ' + brand} {wireless}",
            ],
        )
        desc = f"Som limpo, {'conexão bluetooth estável' if wireless == 'bluetooth' else 'cabo P2'}. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "fone",
                "category": "electronics",
                "title": title,
                "description": desc,
                "price": rng.randrange(60, 1500),
                "attrs": {"marca": brand.lower(), "conexao": wireless},
            }
        )
    return out


def _chairs(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(8):
        style = _pick(rng, ["gamer", "escritorio"])
        title = _pick(
            rng,
            [
                f"Cadeira {'gamer' if style == 'gamer' else 'de escritório'} reclinável",
                f"Cadeira {'gamer' if style == 'gamer' else 'ergonômica para escritório'} com rodinhas",
                f"Cadeira {'gamer RGB' if style == 'gamer' else 'presidente'}",
            ],
        )
        desc = f"Ajuste de altura, apoio de braço, {'encosto reclinável' if style == 'gamer' else 'apoio lombar'}. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "cadeira",
                "category": "furniture",
                "title": title,
                "description": desc,
                "price": rng.randrange(250, 1800),
                "attrs": {"estilo": style},
            }
        )
    return out


def _strollers(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(6):
        brand = _pick(rng, ["Burigotto", "Galzerano", "Chicco"])
        title = _pick(
            rng,
            [
                f"Carrinho de bebê {brand}",
                f"Carrinho {brand} com bebê conforto",
                f"Travel system {brand}",
            ],
        )
        desc = f"Fecha fácil, cesto grande, reclina em 3 posições. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "carrinho_bebe",
                "category": "baby",
                "title": title,
                "description": desc,
                "price": rng.randrange(300, 2000),
                "attrs": {"marca": brand.lower()},
            }
        )
    return out


def _kitchen(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        kind = _pick(rng, ["air_fryer", "liquidificador"])
        brand = _pick(rng, ["Mondial", "Philips Walita", "Britânia", "Arno"])
        if kind == "air_fryer":
            title = _pick(
                rng,
                [
                    f"Air fryer {brand} 4 litros",
                    f"Fritadeira elétrica sem óleo {brand}",
                    f"Fritadeira air fryer {brand}",
                ],
            )
            desc = f"Frita sem óleo, cesto antiaderente, 110v. {_pick(rng, CONDITION_PHRASES)}"
        else:
            title = _pick(
                rng, [f"Liquidificador {brand} 12 velocidades", f"Liquidificador {brand} 1000W"]
            )
            desc = f"Copo de 3 litros, lâminas de aço. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": kind,
                "category": "home",
                "title": title,
                "description": desc,
                "price": rng.randrange(90, 700),
                "attrs": {"marca": brand.lower()},
            }
        )
    return out


def _tvs(rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for _ in range(10):
        brand = _pick(rng, ["Samsung", "LG", "TCL", "Philco"])
        inches = _pick(rng, ["32", "43", "50", "55"])
        uhd = inches in ("50", "55") or rng.random() < 0.3
        title = _pick(
            rng,
            [
                f"Smart TV {brand} {inches} polegadas{' 4K' if uhd else ''}",
                f'TV {inches}" {brand} {"4K UHD" if uhd else "Full HD"}',
                f"Televisão {brand} {inches} pol smart",
            ],
        )
        desc = f"Imagem {'4K' if uhd else 'Full HD'}, Wi-Fi, apps de streaming. {_pick(rng, CONDITION_PHRASES)}"
        out.append(
            {
                "kind": "tv",
                "category": "electronics",
                "title": title,
                "description": desc,
                "price": rng.randrange(700, 4500),
                "attrs": {
                    "marca": brand.lower(),
                    "polegadas": inches,
                    "resolucao": "4k" if uhd else "fullhd",
                },
            }
        )
    return out


GENERATORS = [
    _bikes,
    _phones,
    _laptops,
    _fridges,
    _sofas,
    _tables,
    _sneakers,
    _guitars,
    _books,
    _consoles,
    _cameras,
    _headphones,
    _chairs,
    _strollers,
    _kitchen,
    _tvs,
]


def catalog() -> list[Item]:
    rng = random.Random(SEED)
    items: list[Item] = []
    for generate in GENERATORS:
        for n, raw in enumerate(generate(rng)):
            items.append(
                Item(
                    key=f"{raw['kind']}-{n:02d}",
                    kind=raw["kind"],
                    category=raw["category"],
                    title=raw["title"][:120],
                    description=f"{raw['description']} {_pick(rng, DELIVERY_PHRASES)}".strip(),
                    price_cents=raw["price"] * 100,
                    attrs=raw["attrs"],
                )
            )
    return items


Q = Query
QUERIES: list[Query] = [
    # bikes
    Q("bicicleta aro 29", "bicicleta", {"aro": "29"}),
    Q("bike aro 29", "bicicleta", {"aro": "29"}),
    Q("magrela aro 26", "bicicleta", {"aro": "26"}),
    Q("mountain bike", "bicicleta", {"estilo": "mountain"}),
    Q("bike pra trilha", "bicicleta", {"estilo": "mountain"}),
    Q("bicicleta speed", "bicicleta", {"estilo": "speed"}),
    Q("bicicleta pra ir ao trabalho", "bicicleta", {"estilo": "urbana"}),
    Q("caloi", "bicicleta", {"marca": "caloi"}),
    Q("bicicleat", "bicicleta"),
    Q("bicicleta barata", "bicicleta"),
    # phones
    Q("iphone", "celular", {"marca": "apple"}),
    Q("iphone 128gb", "celular", {"marca": "apple", "armazenamento": "128"}),
    Q("smartphone apple", "celular", {"marca": "apple"}),
    Q("celular samsung", "celular", {"marca": "samsung"}),
    Q("galaxy", "celular", {"marca": "samsung"}),
    Q("telefone celular", "celular"),
    Q("celular com bastante memória 256", "celular", {"armazenamento": "256"}),
    Q("motorola moto g", "celular", {"marca": "motorola"}),
    Q("xiaomi redmi", "celular", {"marca": "xiaomi"}),
    Q("cellular", "celular"),
    # laptops
    Q("notebook", "notebook"),
    Q("notebook 16gb", "notebook", {"ram": "16"}),
    Q("notebook pra programar", "notebook"),
    Q("macbook", "notebook", {"marca": "apple"}),
    Q("laptop dell", "notebook", {"marca": "dell"}),
    Q("computador portátil", "notebook"),
    Q("notbook lenovo", "notebook", {"marca": "lenovo"}),
    # fridges
    Q("geladeira", "geladeira"),
    Q("geladeira duplex", "geladeira", {"portas": "duplex"}),
    Q("refrigerador duas portas", "geladeira", {"portas": "duplex"}),
    Q("geladeira brastemp", "geladeira", {"marca": "brastemp"}),
    Q("gelaedira frost free", "geladeira"),
    Q("fridge", "geladeira"),
    # sofas and tables
    Q("sofá retrátil", "sofa", {"retratil": "true"}),
    Q("sofa 3 lugares", "sofa", {"lugares": "3"}),
    Q("sofá cinza", "sofa", {"cor": "cinza"}),
    Q("estofado para sala", "sofa"),
    Q("mesa de jantar 6 lugares", "mesa", {"lugares": "6"}),
    Q("mesa de vidro", "mesa", {"material": "vidro"}),
    Q("conjunto mesa e cadeiras", "mesa"),
    # sneakers
    Q("tênis de corrida", "tenis", {"uso": "corrida"}),
    Q("sapato pra correr", "tenis", {"uso": "corrida"}),
    Q("tenis nike", "tenis", {"marca": "nike"}),
    Q("tênis 42", "tenis", {"numero": "42"}),
    Q("running shoes", "tenis", {"uso": "corrida"}),
    Q("tenis adidas 40", "tenis", {"marca": "adidas", "numero": "40"}),
    # instruments
    Q("violão", "violao"),
    Q("violao nylon", "violao", {"cordas": "nylon"}),
    Q("guitarra", "guitarra"),
    Q("guitarra fender", "guitarra", {"marca": "fender"}),
    Q("instrumento de corda", ("violao", "guitarra")),
    Q("violão yamaha", "violao", {"marca": "yamaha"}),
    # books
    Q("dom casmurro", "livro", {"obra": "dom casmurro"}),
    Q("livro machado de assis", "livro", {"autor": "machado de assis"}),
    Q("o cortiço", "livro", {"obra": "o cortiço"}),
    Q("clarice lispector", "livro", {"autor": "clarice lispector"}),
    Q("literatura brasileira clássica", "livro"),
    Q("livro vidas secas", "livro", {"obra": "vidas secas"}),
    # consoles
    Q("playstation 5", "console", {"marca": "sony"}),
    Q("ps5", "console", {"marca": "sony"}),
    Q("videogame sony", "console", {"marca": "sony"}),
    Q("xbox", "console", {"marca": "microsoft"}),
    Q("nintendo switch", "console", {"marca": "nintendo"}),
    Q("playstaton", "console", {"marca": "sony"}),
    Q("console de video game", "console"),
    # cameras
    Q("câmera canon", "camera", {"marca": "canon"}),
    Q("camera fotografica", "camera"),
    Q("máquina fotográfica profissional", "camera"),
    Q("nikon", "camera", {"marca": "nikon"}),
    # headphones
    Q("fone bluetooth", "fone", {"conexao": "bluetooth"}),
    Q("fone sem fio", "fone", {"conexao": "bluetooth"}),
    Q("airpods", "fone", {"marca": "apple"}),
    Q("fone jbl", "fone", {"marca": "jbl"}),
    Q("headphone", "fone"),
    # chairs
    Q("cadeira gamer", "cadeira", {"estilo": "gamer"}),
    Q("cadeira para home office", "cadeira", {"estilo": "escritorio"}),
    Q("cadeira de escritório ergonômica", "cadeira", {"estilo": "escritorio"}),
    Q("poltrona pra jogar", "cadeira", {"estilo": "gamer"}),
    # baby
    Q("carrinho de bebê", "carrinho_bebe"),
    Q("carrinho pra bebê com bebê conforto", "carrinho_bebe"),
    Q("travel system", "carrinho_bebe"),
    # kitchen
    Q("air fryer", "air_fryer"),
    Q("fritadeira sem óleo", "air_fryer"),
    Q("airfryer mondial", "air_fryer", {"marca": "mondial"}),
    Q("liquidificador", "liquidificador"),
    Q("eletroportátil para cozinha", ("air_fryer", "liquidificador")),
    # tvs
    Q("smart tv 50", "tv", {"polegadas": "50"}),
    Q("tv 4k", "tv", {"resolucao": "4k"}),
    Q("televisão samsung", "tv", {"marca": "samsung"}),
    Q("tv 55 polegadas", "tv", {"polegadas": "55"}),
    Q("televisor lg", "tv", {"marca": "lg"}),
    # vague or cross-category needs
    Q("presente para quem gosta de música", ("violao", "guitarra", "fone")),
    Q("algo para pedalar", "bicicleta"),
    Q("coisas para o quarto do bebê", "carrinho_bebe"),
    Q("móveis para sala de jantar", "mesa"),
]

# Things the catalog does not sell. A good search returns nothing (or very little) for them; a
# vector search without a distance cutoff would return its nearest listings, however unrelated.
OUT_OF_CATALOG: list[str] = [
    "pneu aro 15 para carro",
    "ração para cachorro",
    "furadeira de impacto",
    "perfume importado",
    "aluguel de apartamento",
    "aula particular de inglês",
    "vaso de planta",
    "ferramentas de jardinagem",
    "material escolar",
    "capacete de moto",
]
