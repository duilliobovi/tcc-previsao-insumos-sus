"""Modelos, utilitários, leitura do Markdown e listagens FTP."""

import tempfile
import time
import unittest
from pathlib import Path

from coletor.cliente_ftp import interpretar_listagem
from coletor.cliente_http import LimitadorPorHost
from coletor.config import ARQUIVO_MD_PADRAO
from coletor.erros import ErroColeta
from coletor.leitor_md import extrair_urls, ler_secoes
from coletor.manifesto import resumir_arquivo
from coletor.modelos import Competencia, Secao
from coletor.util import formatar_bytes, nome_seguro, slug


class TestCompetencia(unittest.TestCase):
    def test_formatos_aceitos_e_ordem(self):
        self.assertEqual(Competencia.de_texto("2008-01"), Competencia(2008, 1))
        self.assertEqual(Competencia.de_texto("202607"), Competencia(2026, 7))
        self.assertEqual(Competencia.de_aamm("0510"), Competencia(2005, 10))
        self.assertLess(Competencia(2025, 12), Competencia(2026, 1))
        self.assertEqual(str(Competencia(2026, 3)), "2026-03")

    def test_rejeita_invalidos(self):
        for texto in ("2026-13", "26-01", "abc"):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                Competencia.de_texto(texto)


class TestUtil(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(slug("Ano/mês_processamento"), "ano_mes_processamento")
        self.assertEqual(slug("SIH/SUS — microdados"), "sih_sus_microdados")

    def test_nome_seguro_bloqueia_path_traversal(self):
        self.assertEqual(nome_seguro("../../etc/passwd"), "passwd")
        self.assertEqual(nome_seguro("a\\b:c.csv"), "b_c.csv")
        with self.assertRaises(ErroColeta):
            nome_seguro("..")

    def test_formatar_bytes(self):
        self.assertEqual(formatar_bytes(None), "?")
        self.assertEqual(formatar_bytes(512), "512 B")
        self.assertEqual(formatar_bytes(1536), "1.5 KB")
        self.assertEqual(formatar_bytes(5 * 1024**3), "5.0 GB")


class TestLeitorMarkdown(unittest.TestCase):
    MD = (
        "# Título\n- https://fora.de/secao\n"
        "## Resultado da comparação\n- `ftp://nao/numerada/`\n"
        "## 1. SIH/SUS — microdados (DS-02)\n"
        "- Diretório: `ftp://ftp.datasus.gov.br/Dados/` e https://a.b/c.pdf.\n"
        "- repetida: https://a.b/c.pdf\n"
        "```bash\nwget \"ftp://x/RD${aa}.dbc\"\n```\n"
        "## 2. Sem URLs\ntexto\n"
    )

    def test_secoes_numeradas_sem_bloco_de_codigo(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp, "d.md")
            caminho.write_text(self.MD, encoding="utf-8")
            secoes = ler_secoes(caminho)
        self.assertEqual(len(secoes), 1)
        self.assertEqual(secoes[0].urls, ("ftp://ftp.datasus.gov.br/Dados/", "https://a.b/c.pdf"))
        self.assertEqual(secoes[0].pasta, "DS-02_sih_sus_microdados")

    def test_pontuacao_final_removida(self):
        self.assertEqual(extrair_urls("veja https://x.y/z.csv; e ftp://h/d/."), ["https://x.y/z.csv", "ftp://h/d/"])

    def test_arquivo_real_do_projeto(self):
        if not ARQUIVO_MD_PADRAO.is_file():
            self.skipTest("datasets_finais.md ausente")
        secoes = ler_secoes(ARQUIVO_MD_PADRAO)
        urls = [u for s in secoes for u in s.urls]
        self.assertGreaterEqual(len(secoes), 10)
        self.assertFalse(any("${" in u or "kaggle.com" in u for u in urls))
        self.assertEqual(len({s.pasta for s in secoes}), len(secoes), "pastas devem ser únicas")

    def test_pasta_sem_codigo(self):
        self.assertEqual(Secao(7, "Outra base", ()).pasta, "07_outra_base")


class TestListagemFtp(unittest.TestCase):
    def test_formatos_iis_e_unix(self):
        entradas = interpretar_listagem([
            "03-10-20  02:43PM               196476 CHBR1901.dbc",
            "11-10-16  10:25AM       <DIR>          200508_",
            "-rwxrwxr-x    1 ftp      ftp        806021 Jan 08  2009 TabelaUnificada_200801.zip",
            "-rwxr-xr-x    1 ftp      ftp         67072 Mar 08  2017 NOTA INFORMATIVA 06-03-2017.doc",
            "drwxr-xr-x    2 ftp      ftp          4096 Mar 08 12:30 relatorios",
            "lixo",
        ])
        self.assertEqual([(e.nome, e.tamanho, e.eh_diretorio) for e in entradas], [
            ("CHBR1901.dbc", 196476, False),
            ("200508_", None, True),
            ("TabelaUnificada_200801.zip", 806021, False),
            ("NOTA INFORMATIVA 06-03-2017.doc", 67072, False),
            ("relatorios", None, True),
        ])


class TestManifestoELimitador(unittest.TestCase):
    def test_resumo_conta_linhas_sem_quebra_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp, "a.csv")
            caminho.write_bytes(b"x;y\n1;2\n3;4")
            digest, linhas = resumir_arquivo(caminho)
        self.assertEqual(linhas, 3)
        self.assertEqual(len(digest), 64)

    def test_limitador_espaca_requisicoes(self):
        limitador = LimitadorPorHost(0.05, {})
        inicio = time.monotonic()
        for _ in range(3):
            limitador.aguardar("h")
        self.assertGreaterEqual(time.monotonic() - inicio, 0.1)


if __name__ == "__main__":
    unittest.main()
