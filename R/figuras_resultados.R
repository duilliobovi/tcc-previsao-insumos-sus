#!/usr/bin/env Rscript
# =============================================================================
# Figuras dos resultados de previsão, comportamento dos algoritmos e estoque, a
# partir das tabelas CSV do pipeline Python (python -m previsao_insumos.pipeline
# e python -m previsao_insumos.comportamento).
#
#   fig_series_nacionais   série nacional dos grupos 06 e 07, com validação e teste
#   fig_mase_modelos       MASE no teste por modelo e horizonte, IC 95% por UF
#   fig_razao_modelos      razão MASE(ML*)/MASE(outro modelo) por horizonte
#   fig_comportamento      ablações e curva de aprendizado do ML*
#   fig_importancia        importância por permutação por grupo de atributos
#   fig_estoque            nível de serviço × estoque médio na simulação com prazo
#   fig_exemplo_previsao   exemplo: SP, medicamentos, previsões e faixa de 90%
#
# Padrão (comentários do orientador e ABNT): sem título nem fonte dentro da
# imagem; legenda abaixo do gráfico; rótulos em pt-BR; 300 dpi; largura útil.
# Uso: Rscript R/figuras_resultados.R   (a partir da raiz do repositório)
# =============================================================================

source(file.path("R", "comum.R"))

resumo_exp <- fromJSON(file.path(DIR_TAB, "resumo_experimento.json"))
venc <- resumo_exp$vencedores_validacao
fases <- resumo_exp$fases
ML_ESTRELA <- names(sort(table(c(venc$h1$`ML*`, venc$h3$`ML*`, venc$h6$`ML*`)), decreasing = TRUE))[1]
familia_de <- function(m) fifelse(grepl("local", m), "ML local",
                          fifelse(m %in% c("Ridge", "Random Forest", "Gradient Boosting"), "ML global", "Estatístico"))
rotulo_h <- function(h) factor(paste0("h = ", h, ifelse(h == 1, " mês", " meses")),
                               levels = c("h = 1 mês", "h = 3 meses", "h = 6 meses"))

# ---- 1. Série nacional ---------------------------------------------------------
painel <- ler("painel_limpo.csv")
nacional <- painel[, .(y = sum(y, na.rm = TRUE) / 1e6), by = .(grupo, data)][, data := as.IDate(paste0(data, "-01"))]
faixa <- function(fase) as.IDate(paste0(range(fases[[fase]]), "-01"))
periodos <- data.table(fase = factor(c("Validação", "Teste"), levels = c("Validação", "Teste")),
                       ini = c(faixa("validacao")[1], faixa("teste")[1]),
                       fim = c(faixa("validacao")[2] + 182, faixa("teste")[2] + 182))
covid <- data.table(ini = as.IDate("2020-03-01"), fim = as.IDate("2021-12-31"))
g1 <- ggplot(nacional, aes(data, y)) +
  geom_rect(data = covid, aes(xmin = ini, xmax = fim, ymin = -Inf, ymax = Inf), inherit.aes = FALSE,
            fill = "grey88") +
  geom_rect(data = periodos, aes(xmin = ini, xmax = fim, ymin = -Inf, ymax = Inf, fill = fase), inherit.aes = FALSE,
            alpha = 0.35) +
  geom_line(colour = "grey15", linewidth = 0.45) +
  facet_wrap(~ grupo, ncol = 1, scales = "free_y", labeller = as_labeller(ROTULO_GRUPO)) +
  scale_fill_manual(values = c("Validação" = "#56B4E9", "Teste" = "#E69F00"),
                    name = "Período (meses-alvo); cinza: COVID-19 (mar. 2020 a dez. 2021)") +
  scale_y_continuous(labels = function(x) num_br(x, if (max(x, na.rm = TRUE) < 10) 1 else 0)) +
  scale_x_date(date_breaks = "2 years", date_labels = "%Y") +
  labs(x = NULL, y = "Quantidade aprovada (milhões por mês)")
salvar(g1, "fig_series_nacionais.png", 16, 10)

# ---- 2. MASE por modelo e horizonte ------------------------------------------------
res <- ler("resumo_metricas_teste.csv")[h %in% c(1, 3, 6)]
res[, familia := familia_de(modelo)]
ordem <- res[, .(m = mean(mase_media)), by = modelo][order(-m), modelo]
res[, modelo := factor(modelo, levels = ordem)][, horizonte := rotulo_h(h)]
LIM_X <- c(0.75, 2.0)
fora <- res[mase_media > LIM_X[2]]
g2 <- ggplot(res, aes(pmin(mase_media, LIM_X[2]), modelo, colour = familia)) +
  geom_vline(xintercept = 1, colour = "grey55", linetype = "dashed", linewidth = 0.35) +
  geom_linerange(aes(xmin = pmin(mase_ic_inf, LIM_X[2]), xmax = pmin(mase_ic_sup, LIM_X[2])), linewidth = 0.5) +
  geom_point(data = res[mase_media <= LIM_X[2]], size = 1.8) +
  geom_point(data = fora, shape = 62, size = 4) +
  geom_label(data = fora, aes(x = LIM_X[2] - 0.08, label = paste("MASE", num_br(mase_media, 2))), hjust = 1, vjust = -0.35,
             size = 2.4, label.size = 0, fill = "white", show.legend = FALSE) +
  facet_wrap(~ horizonte, nrow = 1) + scale_colour_manual(values = CORES_FAMILIA, name = "Família") +
  scale_x_continuous(labels = rotulo_br(1), breaks = c(1, 1.5, 2)) + coord_cartesian(xlim = LIM_X) +
  labs(x = "MASE médio no teste (54 séries)", y = NULL)
salvar(g2, "fig_mase_modelos.png", 16, 9)

# ---- 3. Razão do ML* contra cada modelo ------------------------------------------------
sens <- ler("sensibilidade_ml_contra_cada_modelo.csv")
sens[, horizonte := factor(paste0(rotulo_h(h), "\nML* = ", ml))][, familia := familia_de(comparado)]
sens[, comparado := factor(comparado, levels = sens[, .(r = mean(razao)), by = comparado][order(r), comparado])]
g3 <- ggplot(sens, aes(razao, comparado, colour = familia)) +
  geom_vline(xintercept = 1, colour = "grey40", linewidth = 0.4) +
  geom_vline(xintercept = 0.95, colour = "grey60", linetype = "dotted", linewidth = 0.4) +
  geom_pointrange(aes(xmin = ic_inf, xmax = ic_sup), size = 0.25, linewidth = 0.5) +
  facet_wrap(~ horizonte, nrow = 1) + scale_x_log10(labels = rotulo_br(2)) +
  scale_colour_manual(values = CORES_FAMILIA, name = "Família do modelo comparado") +
  theme(strip.text = element_text(size = rel(0.8)), strip.clip = "off") +
  labs(x = "Razão geométrica MASE(ML*) / MASE(modelo comparado)", y = NULL)
salvar(g3, "fig_razao_modelos.png", 16, 8.5)

# ---- 4. Comportamento: ablações e curva de aprendizado ------------------------------
nomes_var <- c("sem normalização pelo nível" = "Sem normalização pelo nível", "sem calendario" = "Sem mês do ano",
               "sem defasagens" = "Sem defasagens", "sem medias moveis" = "Sem médias de 3 e 6 meses",
               "sem variacao anual" = "Sem variação anual", "sem covid" = "Sem indicador COVID-19",
               "sem grupo" = "Sem indicador de grupo")
abl <- ler("ablacoes.csv")[variante != "completo"][, variante := nomes_var[variante]]
abl[, horizonte := rotulo_h(h)]
abl[, variante := factor(variante, levels = abl[, .(r = mean(razao_vs_completo)), by = variante][order(r), variante])]
g4a <- ggplot(abl, aes(razao_vs_completo, variante, colour = horizonte, shape = horizonte)) +
  geom_vline(xintercept = 1, colour = "grey40", linewidth = 0.4) + geom_point(size = 2) +
  scale_colour_manual(values = c("#56B4E9", "#0072B2", "#003F6B"), name = NULL) + scale_shape_manual(values = c(16, 17, 15), name = NULL) +
  scale_x_log10(labels = rotulo_br(2)) + labs(x = "MASE da variante / MASE completo", y = NULL, tag = "(a)")
curva <- ler("curvas_aprendizado.csv")[dimensao == "UFs no treino"]
curva <- curva[, .(mase = mean(mase_media), lo = min(mase_media), hi = max(mase_media)), by = .(valor, h)]
curva[, horizonte := rotulo_h(h)][, valor := as.numeric(valor)]
g4b <- ggplot(curva, aes(valor, mase, colour = horizonte)) +
  geom_linerange(aes(ymin = lo, ymax = hi), linewidth = 0.5, show.legend = FALSE) + geom_line(linewidth = 0.5) +
  geom_point(aes(shape = horizonte), size = 2) +
  scale_colour_manual(values = c("h = 1 mês" = "#56B4E9", "h = 3 meses" = "#0072B2", "h = 6 meses" = "#003F6B"),
                      name = NULL, drop = FALSE) +
  scale_shape_manual(values = c("h = 1 mês" = 16, "h = 3 meses" = 17, "h = 6 meses" = 15), name = NULL, drop = FALSE) +
  scale_y_continuous(labels = rotulo_br(2)) + scale_x_continuous(breaks = c(7, 14, 20, 27)) +
  labs(x = "UFs no treino", y = "MASE médio (54 séries)", tag = "(b)")
salvar(g4a + (g4b + theme(legend.position = "none")) + plot_layout(widths = c(1.5, 1)),
       "fig_comportamento.png", 16, 8)

# ---- 5. Importância por permutação ------------------------------------------------------
imp <- ler("importancia_permutacao.csv")[!grupo %in% c("calendario", "covid")]
nomes_grupo <- c(defasagens = "Defasagens (12 meses)", medias_moveis = "Médias de 3 e 6 meses",
                 variacao_anual = "Variação anual", calendario = "Mês do ano", covid = "Indicador COVID-19",
                 grupo = "Grupo (06 ou 07)")
imp[, grupo := factor(nomes_grupo[grupo], levels = nomes_grupo[imp[h == 1][order(mean), grupo]])][, horizonte := rotulo_h(h)]
g5 <- ggplot(imp, aes(mean, grupo, fill = horizonte)) +
  geom_col(position = position_dodge(width = 0.75), width = 0.7) +
  geom_errorbar(aes(xmin = mean - std, xmax = mean + std), position = position_dodge(width = 0.75), width = 0.25,
                linewidth = 0.3, colour = "grey30") +
  scale_fill_manual(values = c("#56B4E9", "#0072B2", "#003F6B"), name = NULL) +
  scale_x_continuous(labels = rotulo_br(2)) + labs(x = "Aumento do MASE ao embaralhar o grupo de atributos", y = NULL)
salvar(g5, "fig_importancia.png", 16, 6.5)

# ---- 6. Estoque --------------------------------------------------------------------------
est <- ler("simulacao_estoque_resumo.csv")
est[, familia := familia_de(modelo)][, prazo := factor(paste("Prazo de compra:", L, "meses"))]
est[, servico := factor(paste0("τ = ", num_br(tau, 2)))]
destaque <- unique(c(venc$L3$`E*`, venc$L3$`ML*`, venc$L6$`E*`, venc$L6$`ML*`))
g6 <- ggplot(est, aes(cobertura_meses_mediana, fill_rate_agregado, colour = familia)) +
  geom_line(aes(group = modelo), linewidth = 0.35, alpha = 0.7) +
  geom_point(aes(shape = servico), size = 2) +
  ggrepel::geom_label_repel(data = est[tau == 0.9 & modelo %in% destaque], aes(label = modelo), size = 2.5,
                            seed = 2026, show.legend = FALSE, min.segment.length = 0, box.padding = 0.6,
                            nudge_y = -0.0015, label.size = 0.15, fill = alpha("white", 0.85)) +
  facet_wrap(~ prazo) + scale_colour_manual(values = CORES_FAMILIA, name = NULL) +
  scale_shape_manual(values = c(16, 17), name = "Nível-alvo") +
  scale_y_continuous(labels = pct_br(1)) + scale_x_continuous(labels = rotulo_br(1)) +
  labs(x = "Estoque médio em meses de demanda (mediana das séries)", y = "Demanda atendida (todas as séries)")
salvar(g6, "fig_estoque.png", 16, 8.5)

# ---- 7. Exemplo de previsão ----------------------------------------------------------------
prev <- fread(file.path(DIR_TAB, "previsoes.csv"), encoding = "UTF-8",
              select = c("serie", "modelo", "h", "fase", "data_alvo", "observado", "previsto", "prev_q0.05", "prev_q0.95"))
exemplo <- prev[serie == "06_medicamentos|SP" & h == 1 & fase == "teste" & modelo %in% c(venc$h1$`ML*`, venc$h1$`E*`)]
exemplo[, data := as.IDate(paste0(data_alvo, "-01"))]
hist <- painel[serie == "06_medicamentos|SP"][, data := as.IDate(paste0(data, "-01"))][data >= as.IDate("2021-01-01")]
cores_ex <- setNames(c(CORES_FAMILIA[["ML global"]], CORES_FAMILIA[["Estatístico"]]), c(venc$h1$`ML*`, venc$h1$`E*`))
g7 <- ggplot() +
  geom_ribbon(data = exemplo, aes(data, ymin = `prev_q0.05` / 1e6, ymax = `prev_q0.95` / 1e6, fill = modelo),
              alpha = 0.18) +
  geom_line(data = hist, aes(data, y / 1e6), colour = "grey15", linewidth = 0.45) +
  geom_line(data = exemplo, aes(data, previsto / 1e6, colour = modelo), linewidth = 0.55) +
  geom_point(data = exemplo, aes(data, previsto / 1e6, colour = modelo), size = 1) +
  scale_colour_manual(values = cores_ex, name = "Previsão 1 mês à frente") +
  scale_fill_manual(values = cores_ex, name = "Faixa de 90%") +
  scale_y_continuous(labels = rotulo_br(1)) + scale_x_date(date_breaks = "6 months", date_labels = "%m/%Y") +
  guides(colour = guide_legend(order = 1), fill = guide_legend(order = 2)) +
  theme(legend.box = "vertical", legend.spacing.y = unit(0.05, "cm")) +
  labs(x = NULL, y = "Milhões de unidades")
salvar(g7, "fig_exemplo_previsao.png", 16, 8)
cat("Figuras gravadas em", DIR_FIG, "\n")
