#!/usr/bin/env Rscript
# =============================================================================
# Diferenças regionais no desempenho preditivo (estudo ecológico, exploratório).
#
# Pergunta: a vantagem do melhor ML sobre o melhor modelo estatístico, e a própria
# dificuldade de previsão, são homogêneas entre as UFs, ou se agrupam no espaço?
#
#   * ganho: log(MASE_ML* / MASE_E*), média dos horizontes 1, 3 e 6 e dos dois
#     grupos da UF (negativo = ML* erra menos);
#   * dificuldade: log do MASE do ML* (média dos horizontes e grupos);
#   * regressão de cada medida, por UF, sobre log do volume mensal, % de meses
#     ausentes e força da tendência (achado E4: separar volume e qualidade do
#     registro de um efeito "regional");
#   * Moran global das medidas e dos resíduos (9 999 permutações; Holm entre os
#     quatro índices) e LISA com Benjamini-Hochberg dentro de cada medida (E3).
#
# Entrada: resultados/tabelas/regional_por_serie.csv (pipeline Python).
# Saída:   resultados/tabelas/regional_*.csv e figuras fig_mapa_regional.png,
#          fig_ganho_tendencia.png.
# Uso:     Rscript R/diferencas_regionais.R   (a partir da raiz do repositório)
# =============================================================================

source(file.path("R", "comum.R"))
set.seed(2026)

serie <- ler("regional_por_serie.csv")
por_uf <- serie[, .(ganho = mean(log_razao_media), dificuldade = mean(log(mase_ml)), log_volume = mean(log_volume),
                    pct_ausentes = mean(pct_ausentes), forca_tendencia = mean(forca_tendencia)), by = .(sigla = uf)]
por_uf <- merge(UFS, por_uf, by = "sigla")[match(UFS$sigla, sigla)]
stopifnot(nrow(por_uf) == 27)

# ---- Regressões por UF (N = 27) -------------------------------------------------
regressao <- rbindlist(lapply(c("ganho", "dificuldade"), function(y) {
  ajuste <- lm(reformulate(c("log_volume", "pct_ausentes", "forca_tendencia"), y), data = por_uf)
  set(por_uf, j = paste0("residuo_", y), value = unname(residuals(ajuste)))
  ct <- summary(ajuste)$coefficients
  ic <- confint(ajuste)
  data.table(medida = y, termo = rownames(ct), estimativa = ct[, 1], erro_padrao = ct[, 2], p = ct[, 4],
             ic_inf = ic[, 1], ic_sup = ic[, 2], r2 = summary(ajuste)$r.squared)
}))
fwrite(regressao, file.path(DIR_TAB, "regional_regressao.csv"))

# ---- Diferenças entre regiões (descritivo + Kruskal-Wallis) -------------------------
kw <- rbindlist(lapply(c("ganho", "dificuldade"), function(y) {
  k <- kruskal.test(por_uf[[y]], por_uf$regiao)
  data.table(medida = y, qui2 = unname(k$statistic), gl = unname(k$parameter), p = k$p.value)
}))
fwrite(kw, file.path(DIR_TAB, "regional_kruskal_wallis.csv"))
fwrite(por_uf[, .(razao_media = exp(mean(ganho)), mase_ml_media = exp(mean(dificuldade)), n_ufs = .N), by = regiao],
       file.path(DIR_TAB, "regional_resumo_regiao.csv"))

# ---- Moran global e LISA -----------------------------------------------------------
vertices <- carregar_malha()
W <- matriz_vizinhanca(vertices)
stopifnot(identical(rownames(W), por_uf$sigla))
variaveis <- c("ganho", "dificuldade", "residuo_ganho", "residuo_dificuldade")
moran <- rbindlist(lapply(variaveis, function(v) {
  m <- moran_global(por_uf[[v]], W)
  data.table(variavel = v, I = m$I, esperado = m$esperado, p_permutacao = m$p_bilateral)
}))
moran[, p_holm := p.adjust(p_permutacao, "holm")]
fwrite(moran, file.path(DIR_TAB, "regional_moran.csv"))

lisas <- rbindlist(lapply(c("ganho", "dificuldade"), function(v) {
  l <- lisa(por_uf[[v]], W)
  l[, `:=`(variavel = v, p_bh = p.adjust(p, "BH"))]
}))
lisas[, cluster := fifelse(p_bh <= 0.05, quadrante, "Não significativo")]
fwrite(lisas, file.path(DIR_TAB, "regional_lisa.csv"))
fwrite(por_uf, file.path(DIR_TAB, "regional_uf.csv"))

# ---- Figuras ------------------------------------------------------------------------
centros <- centroides_ufs(vertices)
mapa <- merge(vertices, por_uf[, .(cod, razao = exp(ganho), mase = exp(dificuldade))], by = "cod")
rot <- merge(centros, por_uf[, .(cod, razao = exp(ganho), mase = exp(dificuldade))], by = "cod")
PEQUENAS <- c("RN", "PB", "PE", "AL", "SE", "DF", "ES", "RJ")
desenhar <- function(campo, escala, titulo_legenda) {
  ggplot(mapa, aes(long, lat)) +
    geom_polygon(aes(group = grupo, subgroup = anel, fill = .data[[campo]]), colour = "white", linewidth = 0.25) +
    geom_label(data = rot[!sigla %in% PEQUENAS], aes(cx, cy, label = sigla), size = 2.1, linewidth = 0,
               fill = alpha("white", 0.75), label.padding = unit(0.06, "lines"), colour = "grey10") +
    ggrepel::geom_text_repel(data = rot[sigla %in% PEQUENAS], aes(cx, cy, label = sigla), size = 2.1, colour = "grey10",
                             nudge_x = 4.5, direction = "y", hjust = 0, segment.size = 0.25, segment.colour = "grey40",
                             min.segment.length = 0, seed = 2026) +
    escala + coord_quickmap() + labs(fill = titulo_legenda, x = NULL, y = NULL) +
    theme(axis.text = element_blank(), panel.grid = element_blank(), panel.grid.major = element_blank(),
          legend.key.width = unit(1.1, "cm"), legend.title.position = "top")
}
limite <- max(abs(log(range(mapa$razao))))
painel <- desenhar("razao", scale_fill_gradient2(low = "#2166AC", mid = "#F7F7F7", high = "#B2182B", midpoint = 0,
                                                 trans = "log", limits = exp(c(-limite, limite)),
                                                 labels = rotulo_br(2), breaks = c(0.6, 0.8, 1, 1.25, 1.6)),
                   "(a) Razão MASE(ML*) / MASE(E*)") |
  desenhar("mase", scale_fill_distiller(palette = "YlOrBr", direction = 1, labels = rotulo_br(1)),
           "(b) MASE do ML*")
salvar(painel, "fig_mapa_regional.png", 16, 9.5)

dispersao <- ggplot(por_uf, aes(forca_tendencia, exp(ganho))) +
  geom_hline(yintercept = 1, colour = "grey50", linetype = "dashed", linewidth = 0.4) +
  geom_smooth(method = "lm", formula = y ~ x, colour = "grey30", fill = "grey85", linewidth = 0.5) +
  geom_point(aes(colour = regiao), size = 2.2) +
  ggrepel::geom_text_repel(aes(label = sigla, colour = regiao), size = 2.6, seed = 2026, show.legend = FALSE,
                           max.overlaps = 30, min.segment.length = 0.2) +
  scale_y_log10(labels = rotulo_br(2)) + scale_colour_manual(values = CORES_REGIAO) +
  scale_x_continuous(labels = rotulo_br(1)) +
  labs(x = "Força da tendência (média dos dois grupos da UF, antes da validação)",
       y = "Razão MASE(ML*) / MASE(E*)", colour = "Região")
salvar(dispersao, "fig_ganho_tendencia.png", 16, 8.5)

cat("Moran:\n"); print(moran)
cat("LISA significativos (BH):\n"); print(lisas[cluster != "Não significativo", .(variavel, sigla, quadrante, p, p_bh)])
print(regressao[termo != "(Intercept)"])
