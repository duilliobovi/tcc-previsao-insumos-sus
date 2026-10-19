#!/usr/bin/env Rscript
# =============================================================================
# Conferência independente das linhas de base em R (pacote forecast) contra o
# Python (statsmodels), nas mesmas séries, origens e horizontes do teste.
#
# * Ingênuo sazonal: deve coincidir exatamente (mesma regra determinística);
# * ETS aditivo amortecido (AAdA) em log1p: estimadores diferentes (forecast::ets
#   × statsmodels), então se espera concordância aproximada, e não identidade.
#
# Saída: resultados/tabelas/conferencia_r_python.csv
# Uso:   Rscript R/conferencia_baselines.R   (a partir da raiz do repositório)
# =============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(forecast)
})

args_script <- commandArgs(trailingOnly = FALSE)
arquivo_script <- sub("^--file=", "", grep("^--file=", args_script, value = TRUE))
DIR_REPO <- dirname(dirname(normalizePath(if (length(arquivo_script)) arquivo_script else "R/x.R", mustWork = FALSE)))
DIR_TAB <- file.path(DIR_REPO, "resultados", "tabelas")

painel <- fread(file.path(DIR_TAB, "painel_limpo.csv"))
prev_py <- fread(file.path(DIR_TAB, "previsoes.csv"))[fase == "teste" & modelo %in% c("Ingênuo sazonal", "ETS")]
datas <- sort(unique(painel$data))

# Mesma imputação do Python: interpolação linear e valor mais próximo nas pontas.
interpolar <- function(y) approx(seq_along(y), y, seq_along(y), rule = 2)$y

origens <- sort(unique(prev_py$origem))
registros <- list()
for (s in unique(prev_py$serie)) {
  y <- painel[serie == s][order(data), y]
  for (o in origens) {
    historico <- interpolar(y[seq_len(o + 1)])               # índice 0-based do Python → o + 1 valores
    z <- ts(log1p(historico), frequency = 12)
    p_snaive <- as.numeric(snaive(ts(historico, frequency = 12), h = 6)$mean)
    p_ets <- tryCatch(expm1(as.numeric(forecast(ets(z, model = "AAA", damped = TRUE), h = 6)$mean)),
                      error = function(e) p_snaive)
    for (h in c(1, 3, 6)) {
      registros[[length(registros) + 1]] <- data.table(serie = s, origem = o, h = h,
                                                       modelo = c("Ingênuo sazonal", "ETS"),
                                                       previsto_r = c(p_snaive[h], p_ets[h]))
    }
  }
}
r <- rbindlist(registros)
comp <- merge(prev_py[, .(serie, origem, h, modelo, previsto, observado, escala)], r, by = c("serie", "origem", "h", "modelo"))
comp <- comp[!is.na(observado)]
conferencia <- comp[, .(
  mase_python = mean(abs(observado - previsto) / escala),
  mase_r = mean(abs(observado - previsto_r) / escala),
  dif_relativa_mediana_previsao = median(abs(previsto_r - previsto) / pmax(previsto, 1)),
  n = .N
), by = .(modelo, h)][order(modelo, h)]
fwrite(conferencia, file.path(DIR_TAB, "conferencia_r_python.csv"))
print(conferencia)
