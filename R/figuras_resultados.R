#!/usr/bin/env Rscript
# =============================================================================
# Figuras dos resultados de previsão (Entrega II), a partir das tabelas CSV
# gravadas pelo pipeline Python (python -m previsao_insumos.pipeline).
#
#   fig09  demanda mensal per capita por região (grupos 06 e 07), com COVID e teste
#   fig10  caracterização: força sazonal × força de tendência por série
#   fig11  MASE no teste por modelo e horizonte, com IC 95% bootstrap
#   fig12  postos médios de Friedman e diferença crítica de Nemenyi
#   fig13  distribuição de Δ_i = MASE(ML*) − MASE(E*) entre as séries
#   fig14  fronteira nível de serviço × estoque (política order-up-to)
#   fig15  exemplo de previsões no teste (SP, medicamentos), com faixa de 90%
#
# Uso: Rscript R/figuras_resultados.R   (a partir da raiz do repositório)
# =============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(jsonlite)
  library(ggplot2)
  library(patchwork)
  library(scales)
  library(ggrepel)
})

args_script <- commandArgs(trailingOnly = FALSE)
arquivo_script <- sub("^--file=", "", grep("^--file=", args_script, value = TRUE))
DIR_REPO <- dirname(dirname(normalizePath(if (length(arquivo_script)) arquivo_script else "R/x.R", mustWork = FALSE)))
DIR_DADOS <- Sys.getenv("TCC_DADOS", file.path(DIR_REPO, "DATASETS"))
DIR_TAB <- file.path(DIR_REPO, "resultados", "tabelas")
DIR_FIG <- file.path(DIR_REPO, "resultados", "figuras")
dir.create(DIR_FIG, recursive = TRUE, showWarnings = FALSE)

# Números nos rótulos com vírgula decimal (padrão brasileiro).
sprintf_br <- function(...) gsub("(\\d)\\.(\\d)", "\\1,\\2", sprintf(...))

ler <- function(nome) fread(file.path(DIR_TAB, nome), encoding = "UTF-8")
resumo_exp <- fromJSON(file.path(DIR_TAB, "resumo_experimento.json"))

CORES_REGIAO <- c(Norte = "#1b9e77", Nordeste = "#d95f02", `Centro-Oeste` = "#7570b3",
                  Sudeste = "#e7298a", Sul = "#66a61e")
CORES_FAMILIA <- c(`Estatístico` = "#2166ac", `Machine learning` = "#b2182b")
ROTULO_GRUPO <- c(`06_medicamentos` = "Grupo 06 — Medicamentos", `07_opm` = "Grupo 07 — Órteses, próteses e materiais")
FONTE_SIA <- "Fonte: elaborado pelos autores com dados do SIA/SUS (TabNet, quantidade aprovada) e IBGE."
BASELINES <- c("Ingênuo sazonal", "ETS", "SARIMA", "Theta")

tema <- theme_minimal(base_size = 10) +
  theme(plot.title = element_text(face = "bold", size = 11), legend.position = "bottom",
        plot.caption = element_text(hjust = 0, size = 7, colour = "grey30"),
        plot.background = element_rect(fill = "white", colour = NA))

salvar <- function(g, nome, largura = 16, altura = 11) {
  # No TCC, título e fonte ficam no documento (ABNT); a imagem guarda só o subtítulo com as estatísticas.
  if (Sys.getenv("FIGURAS_SEM_TITULO", "1") == "1") {
    g <- if (inherits(g, "patchwork")) g + plot_annotation(title = NULL, caption = NULL)
         else g + labs(title = NULL, caption = NULL)
  }
  ggsave(file.path(DIR_FIG, nome), g, width = largura, height = altura, units = "cm", dpi = 300, bg = "white")
}

inicio_teste <- as.Date(paste0(resumo_exp$origens_teste[1], "-01"))
fim_teste <- as.Date(paste0(tail(resumo_exp$origens_teste, 1), "-01")) + 6 * 31

# ---- fig09: série regional per capita ---------------------------------------
painel <- ler("painel_limpo.csv")
painel[, data := as.Date(paste0(data, "-01"))]
sidra <- fromJSON(file.path(DIR_DADOS, "DS-11_ibge_estimativas_de_populacao", "sidra_t_6579_n3_all_v_9324_p_all.json"))[-1, ]
siglas <- c(`11` = "RO", `12` = "AC", `13` = "AM", `14` = "RR", `15` = "PA", `16` = "AP", `17` = "TO", `21` = "MA",
            `22` = "PI", `23` = "CE", `24` = "RN", `25` = "PB", `26` = "PE", `27` = "AL", `28` = "SE", `29` = "BA",
            `31` = "MG", `32` = "ES", `33` = "RJ", `35` = "SP", `41` = "PR", `42` = "SC", `43` = "RS", `50` = "MS",
            `51` = "MT", `52` = "GO", `53` = "DF")
populacao <- data.table(uf = siglas[sidra$D1C], ano = as.integer(sidra$D3C), pop = as.numeric(sidra$V))
# Anos sem estimativa (censo 2010/2022) recebem interpolação linear dentro da UF.
grade <- CJ(uf = unique(populacao$uf), ano = 2008:2025)
populacao <- populacao[grade, on = .(uf, ano)]
populacao[, pop := approx(ano[!is.na(pop)], pop[!is.na(pop)], ano, rule = 2)$y, by = uf]
painel[, ano := as.integer(format(data, "%Y"))]
regional <- merge(painel, populacao, by = c("uf", "ano"))[
  , .(taxa = 1e3 * sum(y, na.rm = TRUE) / sum(pop[!is.na(y)])), by = .(grupo, regiao, data)]
regional[, regiao := factor(regiao, names(CORES_REGIAO))]
salvar(
  ggplot(regional, aes(data, taxa, colour = regiao)) +
    annotate("rect", xmin = as.Date("2020-03-01"), xmax = as.Date("2021-12-31"), ymin = -Inf, ymax = Inf,
             fill = "grey80", alpha = 0.5) +
    annotate("rect", xmin = inicio_teste, xmax = fim_teste, ymin = -Inf, ymax = Inf, fill = "#fee8c8", alpha = 0.6) +
    geom_line(linewidth = 0.45) +
    facet_wrap(~grupo, ncol = 1, scales = "free_y", labeller = as_labeller(ROTULO_GRUPO)) +
    scale_colour_manual(values = CORES_REGIAO, name = "Região") +
    scale_y_continuous(labels = label_number(big.mark = ".", decimal.mark = ",")) +
    labs(title = "Demanda mensal aprovada por mil habitantes, por região (2008–2025)",
         subtitle = "Cinza: COVID-19 (mar/2020–dez/2021). Laranja: período de teste",
         x = NULL, y = "Quantidade aprovada por mil hab.", caption = FONTE_SIA) + tema,
  "fig09_series_regionais.png", 16, 13
)

# ---- fig10: caracterização ---------------------------------------------------
carac <- ler("caracterizacao_series.csv")
carac[, c("grupo", "uf") := tstrsplit(serie, "|", fixed = TRUE)]
carac[, regiao := painel[, .(regiao = regiao[1]), by = uf][carac, on = "uf", regiao]]
salvar(
  ggplot(carac, aes(forca_tendencia, forca_sazonal, colour = regiao, shape = grupo)) +
    geom_hline(yintercept = 0.3, linetype = 2, colour = "grey50") +
    geom_point(size = 2.4, alpha = 0.85) +
    geom_text_repel(aes(label = uf), size = 2.1, show.legend = FALSE, seed = 1, max.overlaps = 40,
                    segment.colour = "grey70") +
    scale_colour_manual(values = CORES_REGIAO, name = "Região") +
    scale_shape_manual(values = c(`06_medicamentos` = 16, `07_opm` = 17), labels = ROTULO_GRUPO, name = NULL) +
    coord_cartesian(xlim = c(0, 1), ylim = c(0, 1)) +
    labs(title = "Caracterização das 54 séries (STL robusta, trecho de treino)",
         subtitle = "Linha tracejada: F_S = 0,3, limiar de sazonalidade relevante (Wang; Smith; Hyndman, 2006)",
         x = "Força da tendência (F_T)", y = "Força da sazonalidade (F_S)", caption = FONTE_SIA) + tema +
    guides(colour = guide_legend(nrow = 2), shape = guide_legend(nrow = 2)),
  "fig10_caracterizacao.png", 16, 12
)

# ---- fig11: MASE por modelo e horizonte ---------------------------------------
resumo <- ler("resumo_metricas_teste.csv")
ordem <- resumo[, .(m = mean(mase_media)), by = modelo][order(m), modelo]
resumo[, modelo := factor(modelo, rev(ordem))]
salvar(
  ggplot(resumo, aes(mase_media, modelo, colour = familia)) +
    geom_vline(xintercept = 1, linetype = 2, colour = "grey40") +
    geom_pointrange(aes(xmin = mase_ic_inf, xmax = mase_ic_sup), size = 0.35) +
    facet_wrap(~h, labeller = as_labeller(function(h) paste0("h = ", h, (ifelse(h == "1", " mês", " meses"))))) +
    scale_colour_manual(values = CORES_FAMILIA, name = NULL) +
    labs(title = "Erro de previsão no teste (MASE médio entre 54 séries, IC 95% bootstrap)",
         subtitle = "MASE < 1: melhor que o ingênuo sazonal na amostra; 18 origens de teste",
         x = "MASE", y = NULL, caption = FONTE_SIA) + tema,
  "fig11_mase_modelos.png", 17, 10
)

# ---- fig12: diagrama de diferença crítica ------------------------------------
postos <- ler("postos_friedman.csv")
setnames(postos, 1, "modelo")
postos[, familia := fifelse(modelo %in% BASELINES, "Estatístico", "Machine learning")]
cd <- resumo_exp$nemenyi_cd
melhor <- min(postos$posto_medio)
salvar(
  ggplot(postos, aes(posto_medio, reorder(modelo, -posto_medio), colour = familia)) +
    annotate("rect", xmin = melhor, xmax = melhor + cd, ymin = -Inf, ymax = Inf, fill = "#fdd49e", alpha = 0.5) +
    geom_segment(aes(x = 1, xend = posto_medio, yend = reorder(modelo, -posto_medio)), colour = "grey75") +
    geom_point(size = 3) +
    geom_text(aes(label = number(posto_medio, accuracy = 0.01, decimal.mark = ",")), nudge_y = 0.3, size = 2.8,
              show.legend = FALSE) +
    scale_colour_manual(values = CORES_FAMILIA, name = NULL) +
    labs(title = "Postos médios de Friedman (MASE) e diferença crítica de Nemenyi",
         subtitle = sprintf_br("Iman-Davenport F = %.2f (p < 0,001); faixa: até CD = %.2f do melhor (α = 0,05)",
                            resumo_exp$iman_davenport_F, cd),
         x = "Posto médio (1 = melhor)", y = NULL, caption = FONTE_SIA) + tema,
  "fig12_friedman_nemenyi.png", 16, 10
)

# ---- fig13: distribuição de Δ_i ------------------------------------------------
metricas <- ler("metricas_por_serie.csv")[fase == "teste"]
venc <- resumo_exp$vencedores_validacao
# Δ_i por horizonte com os vencedores escolhidos na validação (o nome `hz` evita
# conflito com a coluna `h` dentro de [.data.table).
dif <- rbindlist(lapply(names(venc), function(hz) {
  w <- dcast(metricas[h == as.integer(hz)], serie ~ modelo, value.var = "mase")
  data.table(serie = w$serie, h = as.integer(hz), d = w[[venc[[hz]]$`ML*`]] - w[[venc[[hz]]$`E*`]])
}))
D <- dif[, .(D = mean(d)), by = serie][, grupo := tstrsplit(serie, "|", fixed = TRUE)[[1]]]
h1 <- ler("testes_hipoteses.csv")[hipotese == "H1"]
salvar(
  ggplot(D, aes(D, fill = grupo)) +
    geom_histogram(bins = 22, colour = "white", alpha = 0.85) +
    geom_vline(xintercept = 0, linetype = 2) +
    annotate("rect", xmin = h1$ic_inf, xmax = h1$ic_sup, ymin = -Inf, ymax = Inf, fill = "black", alpha = 0.12) +
    geom_vline(xintercept = h1$efeito, colour = "black") +
    scale_fill_manual(values = c(`06_medicamentos` = "#e66101", `07_opm` = "#5e3c99"), labels = ROTULO_GRUPO, name = NULL) +
    scale_x_continuous(labels = label_number(accuracy = 0.1, decimal.mark = ",")) +
    scale_y_continuous(breaks = function(l) seq(0, ceiling(l[2]), by = 2)) +
    labs(title = "Diferença de MASE entre o melhor ML e o melhor baseline, por série",
         subtitle = sprintf_br("Δ = %.3f (IC 95%%: %.3f a %.3f); Wilcoxon p = %.1e; Holm p = %.1e",
                            h1$efeito, h1$ic_inf, h1$ic_sup, h1$p_valor, h1$p_holm),
         x = expression(Delta[i] == MASE[i](ML^"*") - MASE[i](E^"*")), y = "Número de séries", caption = FONTE_SIA) + tema,
  "fig13_diferenca_ml_baseline.png", 16, 10
)

# ---- fig14: fronteira de estoque ---------------------------------------------
estoque <- ler("simulacao_estoque_resumo.csv")
estoque[, familia := fifelse(modelo %in% BASELINES, "Estatístico", "Machine learning")]
salvar(
  ggplot(estoque, aes(estoque_relativo, fill_rate, colour = familia, shape = factor(tau))) +
    geom_point(size = 3) +
    geom_text_repel(aes(label = modelo), size = 2.5, show.legend = FALSE, seed = 1, max.overlaps = 30,
                    min.segment.length = 0) +
    scale_colour_manual(values = CORES_FAMILIA, name = NULL) +
    scale_shape_manual(values = c(`0.9` = 16, `0.95` = 17), labels = c("τ = 0,90", "τ = 0,95"), name = "Quantil") +
    scale_x_continuous(labels = label_percent(decimal.mark = ","), expand = expansion(mult = 0.08)) +
    scale_y_continuous(labels = label_percent(accuracy = 0.1, decimal.mark = ",")) +
    labs(title = "Nível de serviço × estoque médio na política order-up-to (h = 1, teste)",
         subtitle = "Medianas das 54 séries; ideal: canto superior esquerdo",
         x = "Estoque médio remanescente (% da demanda)", y = "Fill rate (demanda atendida)", caption = FONTE_SIA) + tema,
  "fig14_estoque.png", 16, 11
)

# ---- fig15: exemplo de previsão ------------------------------------------------
prev <- ler("previsoes.csv")[serie == "06_medicamentos|SP" & h == 1 & fase == "teste"]
alvo <- c(venc[["1"]]$`E*`, venc[["1"]]$`ML*`)
prev <- prev[modelo %in% alvo]
prev[, data := as.Date(paste0(data_alvo, "-01"))]
hist <- painel[serie == "06_medicamentos|SP" & data >= as.Date("2021-01-01")]
salvar(
  ggplot() +
    geom_ribbon(data = prev, aes(data, ymin = `prev_q0.05`, ymax = `prev_q0.95`, fill = modelo), alpha = 0.18) +
    geom_line(data = hist, aes(data, y), colour = "black", linewidth = 0.5) +
    geom_line(data = prev, aes(data, previsto, colour = modelo), linewidth = 0.8) +
    scale_colour_manual(values = setNames(c("#2166ac", "#b2182b"), alvo), name = "Modelo") +
    scale_fill_manual(values = setNames(c("#2166ac", "#b2182b"), alvo), name = "Modelo") +
    scale_y_continuous(labels = label_number(scale = 1e-6, suffix = " mi", decimal.mark = ",")) +
    labs(title = "SP, grupo 06 (medicamentos): observado e previsões um passo à frente no teste",
         subtitle = "Faixas: intervalos de 90% por conformal comum, calibrados na validação",
         x = NULL, y = "Quantidade aprovada", caption = FONTE_SIA) + tema,
  "fig15_exemplo_previsao_sp.png", 16, 10
)
cat("figuras de resultados gravadas em", DIR_FIG, "\n")
