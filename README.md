# Machine learning × modelos estatísticos na previsão da demanda ambulatorial de medicamentos e OPM no SUS

Código, dados derivados e resultados do Trabalho de Conclusão de Curso **"Análise de algoritmos de machine
learning e modelos estatísticos na previsão da demanda ambulatorial de medicamentos e OPM no SUS: desempenho
preditivo, simulação de estoques e diferenças regionais"**, do Bacharelado em Ciência de Dados da Universidade
Virtual do Estado de São Paulo (UNIVESP), 2026.

## Perguntas

1. **Desempenho preditivo.** Algoritmos de *machine learning* (Ridge, Random Forest, Gradient Boosting; global e
   local) erram menos que seis modelos estatísticos (ingênuo, deriva, ingênuo sazonal, Theta, ETS e ARIMA
   automáticos) na previsão mensal da quantidade aprovada no SIA/SUS dos grupos 06 (medicamentos) e 07 (OPM),
   nas 27 UFs, a 1, 3 e 6 meses?
2. **Simulação de estoques.** Com prazo de compra de 3 e 6 meses, o ganho de precisão reduz o custo de estoque?
3. **Diferenças regionais.** O ganho é homogêneo entre as UFs ou se agrupa no espaço?

O plano de análise foi registrado antes da execução do teste: `git show plano-analise-final`
(arquivo [`docs/plano_analise.md`](docs/plano_analise.md)).

## Principais resultados

Os números abaixo são lidos de `resultados/numeros.json`, gerado pelo código (ver `resultados/tabelas/`):
`testes_hipoteses.csv` (hipóteses confirmatórias, com Holm), `resumo_metricas_teste.csv` (erro por modelo e
horizonte), `sensibilidade_ml_contra_cada_modelo.csv`, `simulacao_estoque_resumo.csv` e `regional_*.csv`.

## Estrutura

```
├── webscraping/                 coletor dos dados (TabNet por consulta automatizada, FTP, APIs) e testes
├── dados/FONTES_DE_DADOS.md     portais, consultas e nomes de arquivo (os dados brutos não são versionados)
├── dados/MANIFEST.csv           rastreio da coleta: URL, consulta, data e hora, período, SHA-256 de cada arquivo
├── docs/plano_analise.md        hipóteses, métricas, critério de "desempenho adequado" (registrado com tag)
├── python/previsao_insumos/     previsão, inferência, estoque e comportamento dos algoritmos
│   ├── config.py          parâmetros congelados do experimento
│   ├── dados.py           leitura do TabNet, limpeza (falta de registro × zero), valor unitário
│   ├── caracterizacao.py  STL (forças de tendência e sazonalidade), ADF/KPSS, Syntetos-Boylan
│   ├── modelos.py         linhas de base, atributos, algoritmos e grades de hiperparâmetros
│   ├── avaliacao.py       origem rolante (validação | intervalo | teste), ML global e local, conformal móvel
│   ├── metricas.py        MASE, RMSSE, WAPE, perda quantílica
│   ├── inferencia.py      Wilcoxon por UF, bootstrap por conglomerado e em dois níveis, Holm, BH
│   ├── estoque.py         política order-up-to com prazo de compra; custos pela razão crítica
│   ├── comportamento.py   ablações, curvas de aprendizado, escala, permutação, dependência parcial
│   ├── sensibilidade_local.py  H2 com o Ridge local de penalização ajustada na validação
│   ├── numeros.py         números e tabelas do texto (pt-BR), nunca digitados
│   └── pipeline.py        executa o experimento de ponta a ponta
├── python/tests/          testes automáticos (inclui ausência de vazamento temporal)
├── R/
│   ├── comum.R                    tema gráfico, malha das UFs, vizinhança, Moran e LISA (R base)
│   ├── baselines_automaticas.R    ETS e ARIMA automáticos (pacote forecast)
│   ├── diferencas_regionais.R     ganho e dificuldade por UF, regressão, Moran e LISA com BH
│   └── figuras_resultados.R       figuras do trabalho (ggplot2)
├── reproduzir.sh          comando único: pipeline, comportamento, R e testes
└── resultados/            tabelas CSV, figuras PNG (300 dpi) e numeros.json
```

## Reprodução

Requisitos: Python ≥ 3.11 com `requirements.txt`; R ≥ 4.3 com `data.table`, `jsonlite`, `forecast`,
`ggplot2`, `patchwork`, `scales` e `ggrepel`, nas versões de `renv.lock` (`renv::restore()`); versões exatas do Python em `requirements-lock.txt`.

```bash
python3 -m pip install -r requirements.txt
# 1) Obter os dados (dados/FONTES_DE_DADOS.md) com o coletor:  python3 webscraping/baixar_datasets.py --secoes 'DS-03|DS-11'
# 2) Tudo de uma vez (cerca de 30 a 40 minutos em um computador pessoal de 12 núcleos):
./reproduzir.sh
```

`TCC_DADOS=/caminho/DATASETS` indica outra pasta de dados. O hardware, as versões das bibliotecas e os tempos de
CPU de cada etapa ficam em `resultados/tabelas/resumo_experimento.json` e `tempos_cpu.json`.

## Decisões metodológicas

- **Variável prevista:** quantidade aprovada mensal (SIA/SUS), grupos 06 e 07, 27 UFs, jan. 2008 a abr. 2025;
  as três últimas competências disponíveis (maio a jul. 2025) são descartadas por não estarem consolidadas.
  Mede a demanda atendida e paga, não a necessidade nem o consumo físico.
- **Sem vazamento:** 12 origens de validação, 5 de intervalo e 18 de teste; nenhum mês-alvo da validação coincide
  com um do teste. Hiperparâmetros, E* e ML* são escolhidos só na validação.
- **Inferência:** a UF é a unidade (N = 27); bootstrap por conglomerado de UF; Holm na família confirmatória.
- **Estoque:** revisão mensal com nível-alvo, prazo de 3 e 6 meses, quantis conformais do erro acumulado; valores
  em reais pelo valor aprovado por unidade no SIA/SUS (valor de tabela).

## Autores

Duíllio Campos Bovi, Eder Aparecido Cardoso, Edson Mokiu Yabiku, Felipe Henrique Nunes Teixeira,
Giulia Osório, Mirella Alves dos Santos, Rodrigo Furtado Chaves e Solange Pereira dos Santos Farah.

Orientador: Anderson Nogueira Cotrim.

Licença: MIT (ver `LICENSE`).
