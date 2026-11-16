# webscraping — coletor dos datasets do TCC

Baixa para `DATASETS/` todos os datasets listados em `datasets_finais.md` e registra a
proveniência de cada arquivo em `DATASETS/MANIFEST.csv` (URL, parâmetros, data/hora, competência,
SHA-256, tamanho e número de linhas), como pede a seção 5 do `DATASETS.md`.

## Uso

```bash
cd "~/Área de trabalho/TCC"
python3 -m pip install -r webscraping/requirements.txt   # bs4, lxml, requests (já instalados nesta máquina)

python3 webscraping/baixar_datasets.py --simular                 # plano e volume, sem baixar nada
python3 webscraping/baixar_datasets.py                           # baixa tudo (padrão: microdados de SP, 2008→hoje)
python3 webscraping/baixar_datasets.py --secoes 'DS-05|DS-11'    # só BPS e IBGE
python3 webscraping/baixar_datasets.py --ufs SP,RJ --inicio 2015-01 --fim 2025-12 --tipos RD,AM
python3 webscraping/baixar_datasets.py --help                    # todas as opções
```

Pode ser interrompido com **Ctrl+C** a qualquer momento: as partes já baixadas ficam em
`*.parte` e a próxima execução continua de onde parou. Arquivos completos nunca são baixados
de novo (a não ser com `--forcar`).

| Código de saída | Significado |
|---|---|
| 0 | tudo baixado (ou simulação sem falhas) |
| 1 | terminou, mas alguma URL ou arquivo falhou (ver o resumo e `DATASETS/coleta.log`) |
| 2 | `datasets_finais.md` não encontrado |
| 3 | espaço em disco insuficiente para o plano (nada foi baixado) |
| 130 | interrompido pelo usuário (Ctrl+C) |

### Volume (simulação de 18/10/2026, filtros padrão)

| Pasta em `DATASETS/` | Arquivos | Volume |
|---|---:|---:|
| DS-01 SIH/SUS — TabNet | 90 | consultas |
| DS-02 SIH/SUS — microdados RD e SP | 450 | 14,8 GB |
| DS-03 SIA/SUS — TabNet | 16 | consultas |
| DS-04 SIA/SUS — microdados PA e AM | 719 | 52,3 GB |
| DS-05 BPS (CKAN 2020–2026 + gov.br 1997–2024) | 35 | 165 MB |
| DS-06 Compras.gov.br (manual) | 1 | 1,5 MB |
| DS-07 SIGTAP | 226 | 342 MB |
| DS-08 CNES — Leitos | 225 | 20 MB |
| DS-09 SRAG | 10 | 4,7 GB |
| DS-10 InfoDengue · DS-11 IBGE · DS-12 Monash/USAID | 7 | < 1 MB |
| **Total** | **1.779** | **≈ 72 GB** |

O grosso é o `PA` (produção ambulatorial) de SP. Para economizar espaço, use `--tipos RD,SP,AM,LT`
ou restrinja o período.

## O que cada fonte exige

| Fonte | Técnica |
|---|---|
| FTP DATASUS (SIH, SIA, CNES, SIGTAP) | `LIST` do diretório (formatos IIS e Unix), filtro por tipo/UF/competência pelo nome do `.dbc`, `REST` para retomar |
| Portal de Dados Abertos do SUS (BPS, SRAG) | Next.js: BeautifulSoup lê o JSON de `<script id="__NEXT_DATA__">`; plano B: HTML CKAN clássico; plano C: API `package_show` |
| gov.br — Bases Anuais Compiladas do BPS | Plone: BeautifulSoup percorre a paginação (`b_start`), abre cada `…/view` e extrai o link `@@download/file` e o nome original |
| TabNet (DS-01, DS-03) | BeautifulSoup lê o formulário e o reenvia por POST em ISO-8859-1 (linha = ano/mês, coluna = UF, todas as competências, uma consulta por medida); extrai a tabela `prn` de `<pre>` |
| Zenodo (Monash `car_parts`) | links `/files/` da página do registro; plano B: API REST |
| SIDRA, InfoDengue, Socrata | APIs com URL previsível (InfoDengue: capitais das UFs × dengue/chikungunya/zika) |

URLs de documentação, portais institucionais e buscadores (Swagger do Compras.gov, SIGTAP web,
GitHub…) são **ignoradas com o motivo registrado** no log.

**Fontes fora do ar em 18/10/2026** (o coletor registra a falha e segue): as três páginas
`opendatasus.saude.gov.br/dataset/…` (redirecionam para a home; os mesmos dados de SRAG vêm de
`dadosabertos.saude.gov.br/dataset/srag-2019-a-2026`), `data.usaid.gov` (domínio não resolve) e
`catalog.data.gov/…ghsc-psm…` (404).

## Arquitetura

```
baixar_datasets.py ─► cli.main
                        │
   leitor_md ──► [Secao] ──► planejamento.planejar ──► Plano[Tarefa] ──► sondar_tamanhos
                                  │  (Chain of Responsibility)              │
                                  ▼                                         ▼
     resolvedores/: Referencia → FtpDatasus → Tabnet → Sidra → InfoDengue → Socrata
                    → Zenodo → Ckan → ArquivoDireto → Plone → SemEstrategia
                                                                            │
                         Baixador (ThreadPool, semáforo por host) ◄─────────┘
                           ├── ClienteHttp (robots.txt, Crawl-delay, Retry, Range)
                           ├── ClienteFtp  (conexão por thread, REST, recuo exponencial)
                           └── Manifesto   (MANIFEST.csv, SHA-256)
```

* **Strategy** — cada `Resolvedor` sabe transformar um tipo de URL em `Tarefa`s.
* **Chain of Responsibility** — a primeira estratégia que `aceita()` a URL a resolve; a ordem
  está documentada em `resolvedores/__init__.py`.
* **Protocol** (`Transporte`, `Sondavel`) — o baixador depende de comportamento, não de classes.
* **Injeção de dependência** — `Contexto(config, http, ftp)` é passado aos resolvedores, o que
  permite testá-los com dublês sem rede.
* **Objetos de valor imutáveis** — `Competencia`, `Secao`, `Tarefa`, `Configuracao` são
  `dataclass(frozen=True, slots=True)`.

## Testes

```bash
cd webscraping && python3 -m unittest discover -s tests -t . -v
```

34 testes offline, em menos de 1 s. Cobrem: Markdown, listagens FTP, filtros, TabNet
(serialização ISO-8859-1, `prn`, consulta vazia), CKAN/Next.js, Plone com paginação, deduplicação
do plano, baixador (atomicidade, pular existentes, tamanho divergente) e `ClienteHttp` contra
um servidor HTTP local (robots.txt, Range, retomada, HTML de erro). Também cobrem `ClienteFtp`
com queda de conexão simulada (reconexão + `REST`).

## Rubricas de avaliação (0–100)

### I. Webscraping em Linux

| Peso | Critério |
|---:|---|
| 20 | Execução nativa: shebang, `pathlib`, UTF-8, códigos de saída, Ctrl+C (SIGINT) tratado, log em arquivo |
| 25 | Robustez: timeouts, retentativas com recuo exponencial, retomada (Range/REST), gravação atômica (`.parte` → `os.replace`), validação de tamanho |
| 20 | Cortesia: `robots.txt` (RFC 9309), `Crawl-delay`, limite de conexões e intervalo por host, User-Agent honesto |
| 20 | Eficiência: downloads concorrentes intercalados por host, reuso de conexões FTP, streaming em blocos, verificação de espaço livre antes de começar |
| 15 | Rastreabilidade: `MANIFEST.csv` com SHA-256, log detalhado, modo `--simular` |

### II. Uso da biblioteca BeautifulSoup

| Peso | Critério |
|---:|---|
| 30 | Raspagem de páginas reais e heterogêneas (Next.js, CKAN, Plone paginado, formulário TabNet, Zenodo) |
| 20 | Seletores robustos e com escopo (`select`/`select_one` CSS, `find` com atributos e regex, `#content-core`) |
| 15 | Codificação correta (bytes + `from_encoding`, ISO-8859-1 do DATASUS) e parser adequado (`lxml`) |
| 20 | Reprodução fiel de formulário HTML (selects múltiplos, opção padrão, campos ocultos) |
| 15 | Planos B quando o layout muda e mensagens de erro acionáveis |

### III. Python com código comentado e design patterns

| Peso | Critério |
|---:|---|
| 25 | Arquitetura: Strategy + Chain of Responsibility, `Protocol`, injeção de dependência, separação planejar/baixar |
| 15 | Tipagem completa, dataclasses imutáveis, `StrEnum` |
| 20 | Docstrings e comentários que explicam o *porquê* (decisões, peculiaridades das fontes) |
| 15 | Hierarquia de exceções do domínio e isolamento de falhas |
| 15 | Testes automatizados offline |
| 10 | Idiomas modernos: `pathlib`, context managers, geradores, `:=`, `match`, `removeprefix` |

### Limitações conhecidas (o que separa a implementação de 100)

* A retomada HTTP não usa `If-Range`. Se o arquivo mudar no servidor entre duas execuções, a
  parte é descartada pela validação de tamanho, e não pela comparação de versão.
* Não houve checagem estática de tipos (o `mypy` não está instalado nesta máquina).
* O TabNet depende dos nomes de campo `Linha`/`Coluna`/`Incremento`/`Arquivos`, estáveis há
  décadas, mas não versionados.
* A API de preços do Compras.gov.br exige códigos CATMAT escolhidos pelo pesquisador, por isso não
  entra no download em lote.
