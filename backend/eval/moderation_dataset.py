"""Moderation evaluation set: listings, chat messages and reports, labelled by hand.

Written before the classifiers, so neither the rules nor the prompt were tuned to it. Each part
mixes clear cases with hard ones (``hard=True``): allowed items that look prohibited (a Nerf
gun, a kitchen knife, a painting reproduction), prohibited items in disguise ("pist0la",
"erva"), and honest messages that mention Pix or WhatsApp the way buyers really do.

Categories follow common marketplace policy in Brazil: firearms and ammunition, drugs and
controlled medicines, counterfeits, wild animals, documents and personal data or accounts, and
e-cigarettes (their sale is banned by Anvisa).
"""

from dataclasses import dataclass

LISTING_CATEGORIES = ("weapons", "drugs", "counterfeit", "wildlife", "documents", "tobacco")


@dataclass(frozen=True)
class ListingCase:
    title: str
    description: str
    label: str  # one of LISTING_CATEGORIES, or "ok"
    hard: bool = False


@dataclass(frozen=True)
class MessageCase:
    text: str
    scam: bool
    hard: bool = False


@dataclass(frozen=True)
class ReportCase:
    reason: str  # what the reporter chose: prohibited, scam, offensive, spam, other
    details: str
    target: str  # the listing title and description, or the reported person's latest messages
    priority: int  # 1 urgent (people can lose money or be harmed), 2 review soon, 3 low


L = ListingCase
LISTINGS: list[ListingCase] = [
    # --- weapons ------------------------------------------------------------------------------
    L(
        "Pistola 9mm com 2 carregadores",
        "Arma em ótimo estado, acompanha munição. Sem documentação.",
        "weapons",
    ),
    L("Revólver calibre 38", "Revólver antigo, funcionando. Entrego em mãos.", "weapons"),
    L("Munição .40 caixa com 50", "Caixa fechada, munição original.", "weapons"),
    L("Espingarda calibre 12 de repetição", "Ótima para caça, pouco uso.", "weapons"),
    L(
        "Pist0la de c4no curto",
        "Chama no privado que passo os detalhes. Pronta entrega.",
        "weapons",
        hard=True,
    ),
    L(
        "Kit de conversão para rajada",
        "Peça para transformar semiautomática em automática.",
        "weapons",
        hard=True,
    ),
    L("Soco inglês de aço", "Soco inglês maciço, acabamento cromado.", "weapons"),
    L("Carabina .22 com luneta", "Sem registro, vendo rápido.", "weapons"),
    L("Silenciador universal", "Rosca padrão, serve em várias armas.", "weapons", hard=True),
    L("Taser de choque 800 mil volts", "Arma de choque para defesa pessoal.", "weapons"),
    # allowed, but look like weapons
    L(
        "Lançador Nerf Elite 2.0",
        "Arma de brinquedo com 12 dardos de espuma. Para crianças acima de 8 anos.",
        "ok",
        hard=True,
    ),
    L(
        "Pistola de cola quente 40W",
        "Para artesanato, acompanha 10 bastões de cola.",
        "ok",
        hard=True,
    ),
    L(
        "Faca de cozinha Tramontina 8 polegadas",
        "Faca de chef em inox, nunca usada.",
        "ok",
        hard=True,
    ),
    L("Pistola de pintura elétrica", "Para pintura de paredes e móveis, 600W.", "ok", hard=True),
    L(
        "Réplica decorativa de espada medieval",
        "Espada de parede sem fio, peça de decoração.",
        "ok",
        hard=True,
    ),
    L("Coldre de couro para celular", "Porta-celular de cinto em couro legítimo.", "ok", hard=True),
    L(
        "Alvo para tiro com arco",
        "Alvo de palha 60 cm para arco e flecha esportivo.",
        "ok",
        hard=True,
    ),
    # --- drugs and controlled medicines ------------------------------------------------------
    L("Rivotril 2mg caixa", "Tenho várias caixas, sem receita.", "drugs"),
    L("Ritalina 10mg", "Para estudar melhor, entrego discretamente.", "drugs"),
    L(
        "Erva da boa 25g",
        "Qualidade premium, entrego em todo o Brasil, embalagem discreta.",
        "drugs",
        hard=True,
    ),
    L("Anabolizante Durateston", "Ampolas originais, ciclo completo.", "drugs"),
    L("Óleo de CBD sem receita", "Importado, 3000mg, envio sem prescrição.", "drugs"),
    L("Sementes de maconha feminizadas", "Pacote com 10 sementes, alta germinação.", "drugs"),
    L("Remédio para emagrecer sibutramina", "Caixa com 30 cápsulas, sem receita.", "drugs"),
    L("Balinha (MD) para festas", "Pura, entrego no evento.", "drugs", hard=True),
    L("Ozempic caneta 1mg", "Lacrado, vendo sem receita, aceito Pix.", "drugs"),
    # allowed, but look like drugs
    L("Vitamina C 1000mg 60 comprimidos", "Lacrado, validade 2027.", "ok", hard=True),
    L(
        "Livro Erva-mate: história e cultivo",
        "Livro sobre a erva-mate no Sul do Brasil.",
        "ok",
        hard=True,
    ),
    L("Kit de chás de ervas", "Camomila, erva-cidreira e hortelã, 3 caixas.", "ok", hard=True),
    L("Porta-comprimidos semanal", "Organizador de remédios com 7 dias.", "ok", hard=True),
    L("Whey protein 900g", "Sabor chocolate, lacrado.", "ok", hard=True),
    L("Vaporizador de roupas portátil", "Passadeira a vapor, 1200W.", "ok", hard=True),
    # --- counterfeits --------------------------------------------------------------------------
    L(
        "Tênis Nike Air Jordan réplica primeira linha",
        "Igual ao original, não dá para perceber.",
        "counterfeit",
    ),
    L("Bolsa Louis Vuitton AAA", "Réplica perfeita, com etiqueta e dust bag.", "counterfeit"),
    L("Relógio Rolex Submariner linha A", "Réplica suíça, máquina automática.", "counterfeit"),
    L(
        "Camisa da seleção 1ª linha tailandesa",
        "Mesma qualidade da original, preço de réplica.",
        "counterfeit",
        hard=True,
    ),
    L(
        "Perfume importado contratipo Sauvage",
        "Fragrância inspirada, frasco igual ao original com a marca.",
        "counterfeit",
        hard=True,
    ),
    L("Óculos Ray-Ban paralelo", "Não é original, mas tem a logo e o estojo.", "counterfeit"),
    L("Fone AirPods Pro clone", "Clone com a maçã e número de série.", "counterfeit"),
    L(
        "Notas falsas para cinema",
        "Dinheiro cenográfico que passa como verdadeiro.",
        "counterfeit",
        hard=True,
    ),
    # allowed, but look like counterfeits
    L(
        "Réplica de quadro Monet em tela",
        "Reprodução de A Ponte Japonesa, 60x40, sem assinatura.",
        "ok",
        hard=True,
    ),
    L(
        "Tênis inspirado em Air Force sem marca",
        "Tênis branco de couro, sem logotipo.",
        "ok",
        hard=True,
    ),
    L("Miniatura réplica Fusca 1:24", "Carrinho de coleção em metal.", "ok", hard=True),
    L("Capinha compatível com iPhone 13", "Capa de silicone, marca própria.", "ok", hard=True),
    L("Camisa oficial do Flamengo 2024", "Original com nota fiscal, tamanho M.", "ok"),
    # --- wild animals ---------------------------------------------------------------------------
    L("Filhote de papagaio verdadeiro", "Fala algumas palavras, sem anilha.", "wildlife"),
    L("Mico-leão-dourado filhote", "Muito dócil, acostumado com crianças.", "wildlife"),
    L("Tartaruga tigre-d'água filhote", "Pego no rio, levo até você.", "wildlife", hard=True),
    L("Canário-da-terra cantador", "Pássaro de mato, canta muito.", "wildlife", hard=True),
    L("Jabuti adulto", "Sem documentação do Ibama.", "wildlife"),
    # allowed, but look like wild animals
    L("Gaiola grande para calopsita", "Gaiola de 80 cm com poleiros.", "ok", hard=True),
    L("Pelúcia de arara-azul", "Brinquedo de pelúcia, 40 cm.", "ok", hard=True),
    L("Ração para papagaio 1kg", "Mix de sementes e frutas.", "ok", hard=True),
    L("Livro Aves do Brasil", "Guia de campo ilustrado.", "ok", hard=True),
    # --- documents, data and accounts --------------------------------------------------------
    L("Lista de CPFs com score alto", "Planilha com 10 mil CPFs e dados completos.", "documents"),
    L("Conta Instagram com 50k seguidores", "Transfiro o login e o e-mail.", "documents"),
    L(
        "Carteira de motorista pronta",
        "Sem precisar de autoescola, registrada no Detran.",
        "documents",
    ),
    L("Atestado médico assinado", "Atestado com CID e carimbo, entrego em PDF.", "documents"),
    L("Conta bancária laranja aberta", "Conta pronta para receber Pix.", "documents", hard=True),
    L("Diploma de faculdade reconhecido", "Diploma com registro no MEC sem cursar.", "documents"),
    # allowed, but look like documents
    L("Porta-documentos de couro", "Carteira para CNH e cartões.", "ok", hard=True),
    L("Livro de redação para concursos", "Edição 2025, poucas marcações.", "ok", hard=True),
    L("Luminária de mesa articulada", "Luminária LED para escritório, braço ajustável.", "ok"),
    L(
        "Assinatura anual de revista (cupom)",
        "Cupom de presente para assinatura digital, transferível pela editora.",
        "ok",
        hard=True,
    ),
    # --- e-cigarettes ---------------------------------------------------------------------------
    L("Pod descartável Ignite 5000 puffs", "Vários sabores, lacrado.", "tobacco"),
    L("Vape Elf Bar", "Cigarro eletrônico recarregável com 2 essências.", "tobacco"),
    L("Juice para vaporizador 30ml", "Líquido com nicotina 35mg.", "tobacco", hard=True),
    L(
        "Narguilé completo com essência",
        "Narguilé de 60 cm e 3 essências de tabaco.",
        "tobacco",
        hard=True,
    ),
    # allowed, but look like e-cigarettes
    L("Difusor de aromas ultrassônico", "Umidificador com luz, 300ml.", "ok", hard=True),
    L("Livro Como parar de fumar", "Guia prático, em bom estado.", "ok", hard=True),
    # --- plain allowed listings -----------------------------------------------------------------
    L("Bicicleta aro 29 Caloi", "Quadro de alumínio, 21 marchas.", "ok"),
    L("iPhone 12 128GB", "Bateria 85%, sem marcas.", "ok"),
    L("Sofá retrátil 3 lugares", "Tecido suede cinza.", "ok"),
    L("Notebook Dell Inspiron", "i5, 8GB, SSD 256.", "ok"),
    L("Violão Yamaha C40", "Cordas de nylon novas.", "ok"),
    L("Geladeira Brastemp Frost Free", "375 litros, inox.", "ok"),
    L("Carrinho de bebê Burigotto", "Com bebê conforto.", "ok"),
    L("PlayStation 5 com 2 controles", "Funcionando perfeitamente.", "ok"),
    L("Mesa de jantar 6 lugares", "Madeira maciça.", "ok"),
    L("Livro Dom Casmurro", "Edição de bolso.", "ok"),
    L("Air fryer Philips Walita", "4,1 litros.", "ok"),
    L("Câmera Canon T7 com lente 18-55", "Pouco uso.", "ok"),
]

M = MessageCase
MESSAGES: list[MessageCase] = [
    # --- scams ------------------------------------------------------------------------------------
    M("Faz um Pix direto pra mim que eu te dou 10% de desconto, o site cobra taxa.", True),
    M("Me passa seu WhatsApp que lá a gente fecha por fora, mais barato pros dois.", True),
    M("Já fiz o pagamento, segue o comprovante. Pode enviar o produto hoje?", True, hard=True),
    M(
        "Oi, aqui é do suporte do tradepost. Sua venda foi bloqueada, clique em tradepost-seguro.com para liberar.",
        True,
    ),
    M(
        "Mandei R$ 500 a mais sem querer, me devolve a diferença por Pix que depois o site te paga tudo.",
        True,
    ),
    M(
        "Para garantir a reserva preciso de um sinal de R$ 100 no meu Pix, o restante você paga na entrega.",
        True,
    ),
    M(
        "Pague pelo link do Mercado Pago que eu te mando, é mais seguro que o checkout daqui: mpago-pagamentos.link/abc",
        True,
    ),
    M("Deposita na conta da minha esposa que ela é quem administra as vendas.", True),
    M(
        "O frete eu pago pela transportadora, só preciso que você pague a taxa de liberação de R$ 80.",
        True,
    ),
    M("Seu produto está retido nos Correios, pague a taxa neste site para liberar.", True),
    M("Me manda o código que chegou no seu celular para eu confirmar a compra.", True),
    M("Vou mandar um motoboy buscar, mas antes confirma pra mim os dados do seu cartão.", True),
    M("Compro agora, mas só se você enviar antes de eu pagar, confio em você.", True, hard=True),
    M("Te pago com boleto que gero aqui, é só você aceitar fora do site.", True),
    M("Me chama no zap 11 98765-4321 que te passo a chave Pix.", True),
    M("Ganhei um cupom que só funciona se você confirmar o pagamento pelo meu link.", True),
    M(
        "Sou do setor de segurança, precisamos que você confirme sua senha para não perder a conta.",
        True,
    ),
    M("Transfere pra minha conta que eu já mando o código de rastreio, palavra de honra.", True),
    M("Seu pagamento caiu, mas para liberar o saque você precisa pagar a taxa de R$ 49,90.", True),
    M("Vamos fechar no Pix? Te dou um desconto bom se não passar pelo app.", True),
    M(
        "Envio pela minha transportadora, você paga o frete adiantado direto pra mim.",
        True,
        hard=True,
    ),
    M("Me passa seu e-mail e senha do tradepost que eu finalizo a compra pra você.", True),
    M("Comprei e vou mandar o comprovante do agendamento do Pix, já pode postar.", True, hard=True),
    M("Faz um PIX de R$ 1 pra testar se sua conta recebe, depois mando o resto.", True),
    M("Clica aqui pra ver as fotos reais: bit.ly/fotos-produto-xyz", True),
    # --- honest messages ----------------------------------------------------------------------------
    M("Ainda está disponível?", False),
    M("Aceita Pix pelo checkout do site?", False, hard=True),
    M("Paguei pelo site com Pix agora há pouco, pode conferir aí?", False, hard=True),
    M("Posso retirar pessoalmente amanhã à tarde?", False),
    M("Qual o estado da bateria?", False),
    M("Faz por R$ 900? Posso pagar agora pelo app.", False),
    M("Consegue enviar pelos Correios? Moro em Campinas.", False),
    M("Te mandei uma oferta aqui pelo chat, dá uma olhada.", False),
    M("O frete calculado ficou alto, tem como retirar?", False),
    M("Obrigado, chegou certinho! Já confirmei a entrega.", False),
    M("Tem nota fiscal?", False),
    M("Vou comprar pelo site, só queria confirmar a cor.", False),
    M("Prefiro pagar no cartão em 3x, dá pelo checkout?", False, hard=True),
    M("Pode me mandar mais uma foto do lado de trás?", False),
    M("Já paguei pelo site, quando você posta?", False, hard=True),
    M("Meu WhatsApp está sem bateria, mas sigo por aqui no chat.", False, hard=True),
    M("Aceita troca por um videogame?", False),
    M("Vou deixar meu número só pra combinar a retirada: 11 91234-5678.", False, hard=True),
    M("Comprei aqui mesmo pelo tradepost, o comprovante do pedido está no app.", False, hard=True),
    M("Você entrega no metrô Paraíso?", False),
    M("Desculpa a demora, estava no trabalho. Ainda tem?", False),
    M("Consegue um desconto se eu levar os dois?", False),
    M("A transferência do Pix pelo site já apareceu como paga aqui.", False, hard=True),
    M("Posso pagar o frete à parte se você usar o envio do site?", False, hard=True),
    M("Qual o tamanho exato? Preciso caber no meu carro.", False),
]

R = ReportCase
REPORTS: list[ReportCase] = [
    R("scam", "Pediu Pix por fora e sumiu.", "Me paga no Pix direto que eu envio hoje.", 1),
    R(
        "scam",
        "Mandou link estranho dizendo ser do suporte.",
        "Sua conta foi bloqueada, acesse tradepost-seguro.com.",
        1,
    ),
    R(
        "prohibited",
        "Está vendendo arma.",
        "Revólver calibre 38. Entrego em mãos, sem documento.",
        1,
    ),
    R(
        "prohibited",
        "Remédio controlado sem receita.",
        "Rivotril 2mg, várias caixas, sem receita.",
        1,
    ),
    R("prohibited", "Animal silvestre.", "Filhote de papagaio sem anilha.", 1),
    R("prohibited", "Réplica de marca.", "Bolsa Louis Vuitton AAA, réplica perfeita.", 2),
    R("prohibited", "Vape é proibido.", "Pod descartável 5000 puffs, vários sabores.", 2),
    R("offensive", "Me xingou no chat.", "Você é um idiota, some daqui.", 2),
    R("offensive", "Ofensa racial.", "Mensagem com insulto racial contra o comprador.", 1),
    R("spam", "Mesmo anúncio repetido 20 vezes.", "iPhone 12 128GB (anúncio duplicado).", 3),
    R(
        "spam",
        "Propaganda de loja.",
        "Visite minha loja no Instagram @loja123 com tudo mais barato.",
        3,
    ),
    R("other", "Preço muito acima do mercado.", "Bicicleta aro 29 por R$ 15.000.", 3),
    R("other", "Foto não corresponde.", "Sofá retrátil 3 lugares, foto de outro sofá.", 2),
    R(
        "scam",
        "Disse que pagou e mandou comprovante falso.",
        "Já paguei, segue o comprovante, envia hoje.",
        1,
    ),
    R("prohibited", "Venda de contas.", "Conta Instagram com 50k seguidores, transfiro login.", 2),
    R(
        "prohibited",
        "Isso é brinquedo, mas denunciei por engano.",
        "Lançador Nerf Elite com dardos de espuma.",
        3,
    ),
    R("scam", "Pediu sinal para reservar.", "Para reservar preciso de R$ 100 no meu Pix.", 1),
    R("offensive", "Foi grosseiro na negociação.", "Não vou baixar, se não quer não compra.", 3),
    R(
        "spam",
        "Mensagens repetidas oferecendo outros produtos.",
        "Tenho iPhone, notebook e TV, chama no zap.",
        2,
    ),
    R("other", "Vendedor não responde há dias.", "Comprei e não houve resposta.", 2),
    R("prohibited", "Documento falso.", "Carteira de motorista pronta, registrada no Detran.", 1),
    R("scam", "Pediu os dados do cartão.", "Confirma os dados do seu cartão para o motoboy.", 1),
    R("other", "Acho que é golpe? Não sei.", "Ainda está disponível? Posso retirar amanhã.", 3),
    R("prohibited", "Faca à venda.", "Faca de cozinha Tramontina 8 polegadas, nunca usada.", 3),
    R("offensive", "Ameaça.", "Se não devolver meu dinheiro vou te achar.", 1),
    R(
        "spam",
        "Link de afiliado em todo anúncio.",
        "Compre pelo meu link de afiliado na descrição.",
        3,
    ),
    R(
        "scam",
        "Mandou cobrar taxa de liberação.",
        "Pague R$ 49,90 para liberar o saque da venda.",
        1,
    ),
    R("other", "Item diferente do anunciado.", "Notebook Dell i5 que chegou com i3.", 2),
    R("prohibited", "Medicamento para emagrecer.", "Ozempic caneta 1mg, sem receita.", 1),
    R(
        "spam",
        "Título com palavras-chave sem sentido.",
        "iphone samsung xiaomi barato promoção frete grátis.",
        3,
    ),
]
