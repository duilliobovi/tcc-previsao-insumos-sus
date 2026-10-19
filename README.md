# Previsão de demanda de insumos do SUS: machine learning × estatística, com análise espacial

Código das análises estatísticas e dos algoritmos do Trabalho de Conclusão de Curso **"Análise de
algoritmos de machine learning para previsão de demanda de insumos hospitalares"**, do Bacharelado em
Ciência de Dados da Universidade Virtual do Estado de São Paulo (UNIVESP), 2026, em sua segunda entrega
(relatório parcial).

**Perguntas de pesquisa**

1. Algoritmos de *machine learning* superam métodos estatísticos clássicos na previsão mensal da demanda
   de medicamentos (grupo 06) e de órteses, próteses e materiais especiais (grupo 07) do SUS nas 27 UFs?
2. Quais regiões concentram gargalos no abastecimento desses insumos, e esses gargalos se concentram em
   áreas vizinhas?

## Principais resultados (teste: 18 origens, mai./2023–out./2024; 54 séries)

| Resultado | Valor |
|---|---|
| Melhor modelo (posto médio de Friedman) | *Random Forest* global (2,70), seguido de Ridge (2,93) |
| H1: Δ = MASE(ML\*) − MASE(E\*) | −0,175 (IC 95%: −0,263 a −0,096); Wilcoxon, p de Holm = 0,00016 |
| Redução mediana do erro | 10% |
| Estoque com o mesmo nível de serviço (τ = 0,9) | −2,7 p.p. da demanda (Wilcoxon, p < 0,001) |
| Moran global da dificuldade de previsão (MASE por UF) | I = 0,276; pseudo-p = 0,022 (agrupada no Norte e no Nordeste) |
| Moran global do Índice de Gargalo Logístico (IGL) | I = 0,062; pseudo-p = 0,23 (gargalo heterogêneo) |

## Estrutura

```
├── dados/FONTES_DE_DADOS.md       portais oficiais, consultas e nomes de arquivo (os dados não são versionados)
├── python/previsao_insumos/       previsão e inferência estatística
│   ├── config.py          parâmetros do experimento (horizontes, origens, semente, reamostragens)
│   ├── dados.py           leitura das séries e limpeza (falta de registro × zero, meses incompletos)
│   ├── caracterizacao.py  decomposição STL (forças de tendência e sazonalidade), ADF/KPSS, Syntetos-Boylan
│   ├── modelos.py         ingênuo sazonal, ETS, SARIMA e Theta; Ridge, Random Forest e Gradient Boosting globais
│   ├── avaliacao.py       origem rolante em duas etapas (validação → teste) e intervalos conformais
│   ├── metricas.py        MASE, RMSSE, WAPE e perda quantílica
│   ├── inferencia.py      Wilcoxon, Friedman/Nemenyi, Holm, bootstrap (séries e blocos de origens)
│   ├── estoque.py         simulação de estoque (order-up-to): fill rate × estoque médio
│   └── pipeline.py        executa todas as etapas e grava resultados/tabelas/*.csv
├── python/tests/          testes automáticos (inclui o teste de ausência de vazamento temporal)
├── R/
│   ├── analise_espacial_regional.R   IGL, matriz de vizinhança (malha do IBGE), Moran, LISA e mapas
│   ├── figuras_resultados.R          figuras dos resultados de previsão
│   └── conferencia_baselines.R       reestimação das linhas de base com o pacote forecast
└── resultados/            tabelas CSV e figuras PNG geradas pelo código
```

## Reprodução

**Requisitos:**
- Python ≥ 3.11, com as bibliotecas de `requirements.txt`.
- R ≥ 4.3, com os pacotes `data.table`, `jsonlite`, `readxl`, `read.dbc`, `ggplot2`, `patchwork`,
  `scales`, `ggrepel` e `forecast`.

O pacote `read.dbc` pode ser instalado a partir do arquivo histórico do CRAN.

```bash
python3 -m pip install -r requirements.txt

# 1) Obter os dados conforme dados/FONTES_DE_DADOS.md e gravá-los em DATASETS/

# 2) Previsão e inferência (cerca de 10 minutos com 10 processos)
cd python && python3 -m previsao_insumos.pipeline --processos 10 && cd ..

# 3) Análise espacial, figuras e conferência R × Python
Rscript R/analise_espacial_regional.R
Rscript R/figuras_resultados.R
Rscript R/conferencia_baselines.R

# Testes
(cd python && python3 -m unittest discover -s tests -t .)
```

**Opções:**
- `TCC_DADOS=/caminho/DATASETS` indica outra pasta de dados.
- `FIGURAS_SEM_TITULO=0` mantém título e fonte dentro das imagens. O padrão os omite, porque no trabalho
  eles aparecem no texto, conforme a ABNT.

## Decisões metodológicas

- **Variável prevista:** quantidade aprovada mensal (SIA/SUS), grupos 06 e 07, 27 UFs, jan./2008 a
  abr./2025. As três últimas competências são descartadas por ainda não estarem consolidadas.
- **Escolha sem vazamento:** E\* (melhor modelo estatístico) e ML\* (melhor modelo de *machine learning*)
  são escolhidos nas 6 origens de validação; o teste é executado uma única vez.
- **Inferência sobre as 54 séries:** com 18 origens por série, testes série a série (Diebold-Mariano)
  estariam fora do seu regime assintótico.
- **Análise espacial:** a matriz de contiguidade do tipo rainha é extraída da malha do IBGE e conferida
  com fronteiras conhecidas; o Moran e o LISA usam testes de permutação (9.999 permutações).

## Autores

Duíllio Campos Bovi, Eder Aparecido Cardoso, Edson Mokiu Yabiku, Felipe Henrique Nunes Teixeira,
Giulia Osório, Mirella Alves dos Santos, Rodrigo Furtado Chaves e Solange Pereira dos Santos Farah.

Orientador: Anderson Nogueira Cotrim.

Licença: MIT (ver `LICENSE`).
