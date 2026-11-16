# Plano de análise da versão final

Este plano foi registrado no repositório (commit e tag `plano-analise-final`) **antes** da execução
do período de teste com o protocolo descrito abaixo. A data e o conteúdo podem ser conferidos com
`git show plano-analise-final`. Os parâmetros numéricos estão em `python/previsao_insumos/config.py`.

Limite declarado: o período de teste (origens de maio de 2023 a outubro de 2024) é o mesmo já usado
em uma execução anterior do grupo, com outro protocolo. O registro impede que as escolhas abaixo
sejam ajustadas ao resultado, mas o teste não é cego em relação àquela execução. Não há competências
consolidadas posteriores no TabNet na data de acesso para formar um teste novo.

## 1. Problema e unidade de análise

- Variável prevista: quantidade aprovada mensal no SIA/SUS dos grupos 06 (medicamentos) e 07 (órteses,
  próteses e materiais especiais, OPM), por UF de atendimento; 27 UFs × 2 grupos = 54 séries.
- Período: janeiro de 2008 a abril de 2025 (as três últimas competências disponíveis, maio a julho de
  2025, são descartadas por não estarem consolidadas).
- Unidade inferencial: a UF (N = 27), porque os dois grupos de uma mesma UF compartilham choques de
  registro e de gestão; as séries entram como medidas repetidas dentro da UF.

## 2. Protocolo de previsão

- Origem rolante expansiva; previsão direta para h = 1 a 6 meses em todas as origens.
- 12 origens de validação (dez. 2021 a nov. 2022), 5 origens de intervalo (dez. 2022 a abr. 2023) e
  18 origens de teste (maio 2023 a out. 2024). A última origem de validação está 6 meses antes da
  primeira de teste: nenhum mês-alvo é comum às duas etapas.
- Linhas de base estatísticas (locais): ingênuo, passeio aleatório com deriva, ingênuo sazonal, Theta,
  ETS automático e ARIMA automático (seleção por AICc, com opção não sazonal).
- Machine learning: Ridge, Random Forest e Gradient Boosting por histograma, em versão global (um
  modelo para todas as séries) e local (o mesmo algoritmo, série a série), com os mesmos atributos.
- Hiperparâmetros: grade de 6 configurações por algoritmo, escolhida pelo MASE médio nas origens de
  validação (h = 1, 3, 6). O Gradient Boosting usa parada antecipada dentro do treino.
- E* e ML*: o melhor estatístico e o melhor ML global no MASE médio da validação, por horizonte (e,
  para a simulação de estoque, pela média de h = 1 a L). O teste é executado uma única vez.
- Semente 2026 para o resultado principal; 10 sementes (2026 a 2035) para medir a variabilidade.

## 3. Métricas e critério de "desempenho adequado" (P2)

- Métrica principal: MASE (escala do ingênuo sazonal dentro da amostra). Também: MASE com escala do
  ingênuo simples, RMSSE, WAPE, perda quantílica (τ = 0,9) e viés médio escalado.
- Estimando: razão geométrica, entre as séries, de MASE(ML*)/MASE(E*), por horizonte.
- Critério de superioridade preditiva: razão < 1 com p ajustado por Holm < 0,05.
- Critério de relevância prática, fixado antes do teste: redução de pelo menos 5% (razão ≤ 0,95).
- Critério de utilidade decisória: custo total da política de estoque (manutenção + falta) menor com
  ML* do que com E*, com prazo de compra de 3 e de 6 meses.
- Desempenho "adequado" para o uso em compras exige os três critérios no horizonte da compra.

## 4. Hipóteses confirmatórias (uma família, correção de Holm, α = 0,05)

| Código | Hipótese alternativa | Teste |
|---|---|---|
| H1 (h = 1), H1 (h = 3), H1 (h = 6) | MASE(ML*) < MASE(E*) | Wilcoxon unilateral sobre as médias por UF de log(MASE_ML*/MASE_E*); IC 95% por bootstrap de UFs |
| H2 | o modelo global erra menos que o mesmo algoritmo local | idem, com log(MASE_global/MASE_local), média de h = 1, 3, 6 |
| H3 (L = 3), H3 (L = 6) | custo da política de estoque com ML* < com E* | idem, com log(custo_ML*/custo_E*), τ = 0,9 |

Sensibilidade (não altera a decisão): bootstrap em dois níveis (UFs e blocos de 3 origens) e modelo
misto com intercepto aleatório de UF.

## 5. Análises exploratórias (sem valor confirmatório; correção dentro de cada família)

- ML* contra cada modelo, por horizonte (Holm dentro do horizonte).
- Friedman entre os modelos (UF como bloco) e Wilcoxon par a par contra o melhor, com Holm.
- Concordância de rankings entre MASE e perda quantílica (τ de Kendall, descritivo).
- Cobertura dos intervalos de 90% (conformal em janela móvel), com IC por bootstrap de UFs.
- Erro por segmento (grupo, classe de Syntetos-Boylan, faixa de volume) e variabilidade entre sementes.
- Comportamento dos algoritmos: ablações de atributos, global × local, curvas de aprendizado, custo
  computacional e interpretabilidade (importância por permutação, dependência parcial, coeficientes).
- Diferenças regionais (estudo ecológico): log da razão e MASE por UF; regressão sobre log do volume,
  % de meses ausentes e força da tendência; Moran global dos resíduos (9.999 permutações, Holm entre os
  índices de Moran) e LISA com controle de falsas descobertas (Benjamini-Hochberg).

## 6. Simulação de estoque

Revisão mensal com nível-alvo (order-up-to), prazo de entrega L = 3 e 6 meses, venda perdida, níveis de
serviço-alvo τ = 0,9 e 0,95 (quantis conformais do erro acumulado em L meses). Avaliação nos 18 meses
seguintes à primeira origem de teste. Custos: manutenção por unidade-mês c_m e falta c_f = c_m·τ/(1 − τ).
Valores em reais pelo valor aprovado por unidade no SIA (12 meses antes do teste); custo de capital
anual de 10%, 15% e 20% como cenários.
