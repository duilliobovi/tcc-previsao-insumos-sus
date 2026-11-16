#!/usr/bin/env Rscript
# =============================================================================
# Linhas de base estatísticas com seleção automática (achado A2 do parecer):
#   * ETS automático   — forecast::ets(): erro, tendência (com ou sem amortecimento)
#                        e sazonalidade escolhidos por AICc, incluindo modelos sem
#                        sazonalidade;
#   * ARIMA automático — forecast::auto.arima(): ordens (p, d, q)(P, D, Q)12 por
#                        AICc, com a diferenciação escolhida por testes de raiz
#                        unitária (KPSS e, na parte sazonal, OCSB); a opção não
#                        sazonal está sempre disponível.
#
# Mesmo protocolo das demais linhas de base (python/previsao_insumos/avaliacao.py):
# em cada origem, ajuste só com y[1..origem], em log1p, ausências internas
# interpoladas apenas para o ajuste, previsões h = 1..6 devolvidas com expm1.
#
# Entradas (gravadas pelo pipeline Python): resultados/tabelas/painel_limpo.csv
# e resultados/tabelas/desenho.json.   Saída: resultados/tabelas/previsoes_r.csv
# (e diagnostico_residuos_arima.csv: Ljung-Box na última origem de teste).
#
# Uso:  Rscript R/baselines_automaticas.R [núcleos]   (a partir da raiz do repositório)
# =============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(jsonlite)
  library(forecast)
  library(parallel)
})

args_script <- commandArgs(trailingOnly = FALSE)
arquivo_script <- gsub("~+~", " ", sub("^--file=", "", grep("^--file=", args_script, value = TRUE)), fixed = TRUE)
DIR_REPO <- dirname(dirname(normalizePath(if (length(arquivo_script)) arquivo_script else "R/x.R", mustWork = FALSE)))
DIR_TAB <- file.path(DIR_REPO, "resultados", "tabelas")
nucleos <- as.integer(commandArgs(trailingOnly = TRUE)[1])
if (is.na(nucleos)) nucleos <- max(1L, detectCores() - 2L)

painel <- fread(file.path(DIR_TAB, "painel_limpo.csv"), encoding = "UTF-8")
desenho <- fromJSON(file.path(DIR_TAB, "desenho.json"))
datas <- desenho$datas                       # eixo completo de competências ("2008-01", ...)
origens <- unlist(desenho$origens_indice)    # índices (base 0, como no Python)
H <- desenho$horizonte_maximo
largo <- dcast(painel, data ~ serie, value.var = "y")
setkey(largo, data)
stopifnot(identical(largo$data, datas))

preparar <- function(y) {
  # Remove o trecho anterior ao primeiro registro e interpola ausências internas (só para o ajuste).
  validos <- which(is.finite(y))
  y <- y[min(validos):length(y)]
  ok <- is.finite(y)
  if (!all(ok)) y <- approx(which(ok), y[ok], xout = seq_along(y), rule = 2)$y
  ts(log1p(y), frequency = 12)
}

uma_tarefa <- function(tarefa) {
  y <- largo[[tarefa$serie]][seq_len(tarefa$origem + 1L)]
  z <- preparar(y)
  saida <- list()
  for (modelo in c("ETS automático", "ARIMA automático")) {
    inicio <- proc.time()[["user.self"]]
    prev <- tryCatch({
      ajuste <- if (modelo == "ETS automático") ets(z) else auto.arima(z)
      as.numeric(forecast(ajuste, h = H)$mean)
    }, error = function(e) rep(as.numeric(tail(z, 1)), H))   # falha numérica: cai para o ingênuo
    segundos <- proc.time()[["user.self"]] - inicio
    saida[[modelo]] <- data.table(serie = tarefa$serie, origem = tarefa$origem, h = seq_len(H), modelo = modelo,
                                  previsto = pmax(expm1(prev), 0), cpu_s = segundos / H)
  }
  rbindlist(saida)
}

tarefas <- CJ(serie = setdiff(names(largo), "data"), origem = origens)
lista <- split(tarefas, seq_len(nrow(tarefas)))
inicio <- Sys.time()
res <- rbindlist(mclapply(lista, uma_tarefa, mc.cores = nucleos))
fwrite(res, file.path(DIR_TAB, "previsoes_r.csv"))
cat(sprintf("previsoes_r.csv: %d linhas, %.1f min\n", nrow(res), as.numeric(difftime(Sys.time(), inicio, units = "mins"))))

# Diagnóstico de resíduos (A7): Ljung-Box (24 defasagens) do ARIMA automático na última origem de teste.
ultima <- max(origens)
diag <- rbindlist(mclapply(setdiff(names(largo), "data"), function(s) {
  ajuste <- auto.arima(preparar(largo[[s]][seq_len(ultima + 1L)]))
  lb <- Box.test(residuals(ajuste), lag = 24, type = "Ljung-Box", fitdf = length(coef(ajuste)))
  data.table(serie = s, modelo = paste(arimaorder(ajuste), collapse = ","), p_ljung_box = lb$p.value,
             vies_medio_residuo = mean(residuals(ajuste)))
}, mc.cores = nucleos))
fwrite(diag, file.path(DIR_TAB, "diagnostico_residuos_arima.csv"))
