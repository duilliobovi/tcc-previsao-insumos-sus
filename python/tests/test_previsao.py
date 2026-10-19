"""Testes offline do pipeline: leitura do TabNet, limpeza, métricas, ausência de vazamento e inferência."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from previsao_insumos.dados import ler_tabnet_mensal, marcar_ausencias
from previsao_insumos.inferencia import bootstrap_media, friedman_nemenyi, holm
from previsao_insumos.metricas import escala_sazonal, pinball
from previsao_insumos.modelos import construir_atributos, ingenuo_sazonal

TABNET = '''"Ano/mês processamento";"RO";"SP";"Total"
"2008";30;300;330
"..Janeiro/2008";10;100;110
"..Fevereiro/2008";-;100;100
"Março/2009";10;200;210
"Total";30;300;330
'''


class TestDados(unittest.TestCase):
    def test_le_meses_e_ignora_totais(self):
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp, "t.csv")
            arquivo.write_text(TABNET, encoding="utf-8")
            larga = ler_tabnet_mensal(arquivo)
        self.assertEqual(list(larga.columns), ["RO", "SP"])
        self.assertEqual([str(p) for p in larga.index], ["2008-01", "2008-02", "2009-03"])
        self.assertTrue(np.isnan(larga.loc[pd.Period("2008-02", "M"), "RO"]))

    def test_zero_em_serie_grande_vira_ausente_e_em_serie_pequena_nao(self):
        idx = pd.period_range("2020-01", periods=15, freq="M")
        grande = pd.Series([1000.0] * 14 + [0.0], index=idx)
        pequena = pd.Series([3.0, 0.0] * 7 + [0.0], index=idx)
        self.assertTrue(np.isnan(marcar_ausencias(grande, 50).iloc[-1]))
        self.assertEqual(marcar_ausencias(pequena, 50).iloc[-1], 0.0)


class TestMetricas(unittest.TestCase):
    def test_escala_do_ingenuo_sazonal(self):
        y = np.arange(24, dtype=float)       # diferença sazonal constante = 12
        self.assertEqual(escala_sazonal(y), 12.0)

    def test_pinball_penaliza_mais_a_subprevisao(self):
        self.assertGreater(pinball(np.array([10.0]), np.array([8.0]), 0.9)[0],
                           pinball(np.array([10.0]), np.array([12.0]), 0.9)[0])


class TestSemVazamento(unittest.TestCase):
    def test_atributos_nao_dependem_do_futuro(self):
        z = np.log1p(np.arange(1, 61, dtype=float))
        futuro_alterado = z.copy()
        futuro_alterado[41:] = 999.0                  # muda tudo depois da origem t = 40
        t = np.array([40])
        a, _ = construir_atributos(z, t, 3, np.array([5]), np.array([0]), 0)
        b, _ = construir_atributos(futuro_alterado, t, 3, np.array([5]), np.array([0]), 0)
        np.testing.assert_array_equal(a, b)

    def test_ingenuo_sazonal_repete_o_ano_anterior(self):
        y = np.arange(1, 25, dtype=float)
        np.testing.assert_array_equal(ingenuo_sazonal(y, 3), [13.0, 14.0, 15.0])


class TestInferencia(unittest.TestCase):
    def test_holm_e_monotono_e_limitado(self):
        ajustado = holm({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertAlmostEqual(ajustado["a"], 0.03)
        self.assertAlmostEqual(ajustado["c"], 0.06)
        self.assertAlmostEqual(ajustado["b"], 0.06)

    def test_bootstrap_cobre_a_media(self):
        x = np.random.default_rng(1).normal(0.5, 1, 200)
        inf, sup = bootstrap_media(x, 2000, np.random.default_rng(2))
        self.assertLess(inf, x.mean())
        self.assertGreater(sup, x.mean())

    def test_friedman_ordena_modelos(self):
        perdas = pd.DataFrame({"bom": [1.0, 1.1, 0.9, 1.0], "ruim": [2.0, 2.1, 1.9, 2.2], "medio": [1.5] * 4})
        postos, *_ = friedman_nemenyi(perdas)
        self.assertEqual(list(postos.index), ["bom", "medio", "ruim"])


if __name__ == "__main__":
    unittest.main()
