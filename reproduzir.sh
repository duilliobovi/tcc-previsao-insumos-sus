#!/usr/bin/env bash
# Regenera todas as tabelas, figuras e números do trabalho a partir dos dados em DATASETS/ (ou $TCC_DADOS).
# Uso: ./reproduzir.sh [núcleos]     (a partir da raiz do repositório)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
NUCLEOS="${1:-10}"

echo "== 1/5 Previsão, inferência e simulação de estoque (Python + linhas de base automáticas em R)"
(cd python && python3 -m previsao_insumos.pipeline --processos "$NUCLEOS")

echo "== 2/5 Comportamento dos algoritmos"
(cd python && python3 -m previsao_insumos.comportamento)
(cd python && python3 -m previsao_insumos.sensibilidade_local)   # H2 com o Ridge local ajustado (exploratório)

echo "== 3/5 Diferenças regionais (R)"
Rscript R/diferencas_regionais.R

echo "== 4/5 Figuras (R) e números do texto"
Rscript R/figuras_resultados.R
(cd python && python3 -m previsao_insumos.numeros)

echo "== 5/5 Testes automáticos"
(cd python && python3 -m unittest discover -s tests -t .)
(cd webscraping && python3 -m unittest discover -s tests -t . 2>/dev/null || python3 -m unittest discover -s tests)
echo "Concluído: resultados/tabelas, resultados/figuras e resultados/numeros.json"
