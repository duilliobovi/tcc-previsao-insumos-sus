"""Estratégias de resolução com HTML/FTP falsos (sem rede)."""

import json
import unittest
from urllib.parse import parse_qsl

from bs4 import BeautifulSoup

from coletor.cliente_ftp import EntradaFtp
from coletor.config import Configuracao
from coletor.erros import ErroColeta, SemDados, UrlIgnorada
from coletor.modelos import Competencia, Secao
from coletor.planejamento import planejar
from coletor.resolvedores import criar_resolvedores
from coletor.resolvedores.ckan import ResolvedorCkan, recursos_html_classico, recursos_next_data
from coletor.resolvedores.ftp_datasus import ColecaoDbc, ColecaoSigtap, ResolvedorFtpDatasus
from coletor.resolvedores.plone import ResolvedorPlone
from coletor.resolvedores.tabnet import ResolvedorTabnet, extrair_tabela_prn

from tests.apoio import FtpFalso, HttpFalso, contexto, resposta

SECAO = Secao(1, "Teste (DS-99)", ())


class TestColecoesFtp(unittest.TestCase):
    ENTRADAS = [
        EntradaFtp("RDSP0712.dbc", 1, False),   # antes do início
        EntradaFtp("RDSP0801.dbc", 2, False),
        EntradaFtp("RDRJ0801.dbc", 3, False),   # outra UF
        EntradaFtp("ERSP0801.dbc", 4, False),   # tipo não pedido
        EntradaFtp("PASP2601a.dbc", 5, False),  # fatia de arquivo grande
        EntradaFtp("200508_", None, True),
    ]

    def test_filtra_tipo_uf_e_periodo(self):
        colecao = ColecaoDbc("h", "/d", ("RD", "PA"))
        selecionados = list(colecao.selecionar(self.ENTRADAS, Configuracao(inicio=Competencia(2008, 1))))
        self.assertEqual([(a.nome, a.subpasta) for a in selecionados], [("RDSP0801.dbc", "RD"), ("PASP2601a.dbc", "PA")])

    def test_sigtap_mantem_ultima_versao(self):
        entradas = [
            EntradaFtp("TabelaUnificada_202507_v2508011200.zip", 1, False),
            EntradaFtp("TabelaUnificada_202507_v2509181303.zip", 2, False),
            EntradaFtp("TabelaUnificada_200801.zip", 3, False),
            EntradaFtp("Mapeamento_TUSS_SIGTAP.zip", 4, False),
        ]
        nomes = [a.nome for a in ColecaoSigtap("h", "/d").selecionar(entradas, Configuracao())]
        self.assertEqual(nomes, ["TabelaUnificada_200801.zip", "TabelaUnificada_202507_v2509181303.zip"])

    def test_arquivo_explicito_respeita_filtros(self):
        diretorio = "/dissemin/publicos/SIHSUS/200801_/Dados"
        ftp = FtpFalso({("ftp.datasus.gov.br", diretorio): [EntradaFtp("SPSP2607.dbc", 9, False)]})
        resolvedor = ResolvedorFtpDatasus(contexto(Configuracao(prefixos=frozenset({"RD"})), ftp=ftp))
        url = f"ftp://ftp.datasus.gov.br{diretorio}/SPSP2607.dbc"
        self.assertTrue(resolvedor.aceita(url))
        with self.assertRaises(UrlIgnorada):
            list(resolvedor.resolver(url, SECAO))


class TestTabnet(unittest.TestCase):
    FORMULARIO = """
    <form action="/cgi/tabcgi.exe?sih/cnv/qiuf.def" method="POST">
      <select name="Linha"><option value="Região" selected>Região</option>
        <option value="Ano/mês_atendimento">Ano/mês atendimento</option></select>
      <select name="Coluna"><option value="--Não-Ativa--" selected>Não ativa</option>
        <option value="Unidade_da_Federação">UF</option></select>
      <select name="Incremento" multiple><option value="AIH_aprovadas" selected>AIH</option>
        <option value="Óbitos">Óbitos</option></select>
      <select name="Arquivos" multiple><option value="qiuf2607.dbf" selected>Jul/2026</option>
        <option value="qiuf0712.dbf">Dez/2007</option><option value="qiuf0801.dbf">Jan/2008</option></select>
      <select name="SRegião" multiple><option value="TODAS_AS_CATEGORIAS__" selected>Todas</option>
        <option value="1">Norte</option></select>
      <select name="listahidden2" multiple></select>
      <input type="hidden" name="oculto" value="x">
      <input name="pesqmes2" value="Digite o texto">
      <input type="radio" name="formato" value="table" checked>
    </form>"""
    URL = "http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sih/cnv/qiuf.def"

    def test_monta_uma_consulta_por_medida(self):
        config = Configuracao(linhas_tabnet=("Ano/mês_atendimento", "Inexistente"))
        resolvedor = ResolvedorTabnet(contexto(config, http=HttpFalso({self.URL: self.FORMULARIO})))
        with self.assertLogs("coletor.resolvedores.tabnet", "WARNING"):
            tarefas = list(resolvedor.resolver(self.URL, SECAO))

        self.assertEqual([t.destino.name for t in tarefas], [
            "tabnet_qiuf__ano_mes_atendimento__aih_aprovadas.csv",
            "tabnet_qiuf__ano_mes_atendimento__obitos.csv",
        ])
        campos = parse_qsl(tarefas[1].corpo.decode(), encoding="latin-1")
        self.assertIn(("Coluna", "Unidade_da_Federação"), campos)
        self.assertIn(("Incremento", "Óbitos"), campos)
        self.assertIn(("SRegião", "TODAS_AS_CATEGORIAS__"), campos)
        self.assertIn(("oculto", "x"), campos)
        self.assertIn(("formato", "prn"), campos)
        self.assertNotIn("pesqmes2", dict(campos))
        # Dez/2007 fica fora do período padrão (a partir de 2008-01).
        self.assertEqual([v for k, v in campos if k == "Arquivos"], ["qiuf2607.dbf", "qiuf0801.dbf"])
        self.assertTrue("%D3bitos" in tarefas[1].corpo.decode(), "formulário deve ir em ISO-8859-1")
        self.assertFalse(tarefas[0].estatico)

    def test_extrai_prn_e_detecta_vazio(self):
        tabela = '<html><pre>\n"Ano/mês";"SP"\n"Jan/2026";10\n</pre></html>'.encode("latin-1")
        self.assertEqual(extrair_tabela_prn(resposta(tabela, tipo="text/html")), b'"Ano/m\xc3\xaas";"SP"\n"Jan/2026";10\n')
        with self.assertRaises(SemDados):
            extrair_tabela_prn(resposta(b"<html>Nenhum registro selecionado</html>"))
        with self.assertRaises(ErroColeta):
            extrair_tabela_prn(resposta(b"<html>Erro interno</html>"))


class TestCkanEPlone(unittest.TestCase):
    def test_next_data(self):
        dados = {"props": {"pageProps": {"name": "bps", "resources": [
            {"format": "CSV", "url": "https://s3/BPS/csv/2025_csv.zip"},
            {"format": "JSON", "url": "https://s3/BPS/json/2025_json.zip"},
            {"format": "API", "url": "https://api/v1/#/bps"},
        ]}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(dados)}</script>'
        self.assertEqual(len(recursos_next_data(BeautifulSoup(html, "lxml"))), 3)

        url = "https://dadosabertos.saude.gov.br/dataset/bps"
        resolvedor = ResolvedorCkan(contexto(http=HttpFalso({url: html})))
        tarefas = list(resolvedor.resolver(url, SECAO))
        # Só o CSV passa: JSON fica fora dos formatos padrão e "API" não é arquivo.
        self.assertEqual([t.destino.name for t in tarefas], ["2025_csv.zip"])

    def test_html_classico(self):
        html = """<li class="resource-item"><span class="format-label" data-format="csv"></span>
                  <a class="resource-url-analytics" href="/files/a.csv">baixar</a></li>"""
        self.assertEqual(recursos_html_classico(BeautifulSoup(html, "lxml"), "https://c.gov/dataset/x"),
                         [("csv", "https://c.gov/files/a.csv")])

    def test_plone_paginacao_e_nome_original(self):
        pasta = "https://www.gov.br/s/bases"
        paginas = {
            pasta: f"""<div id="content-core"><a href="{pasta}/ano-2022/view">2022</a>
                       <a href="{pasta}?b_start:int=1">2</a></div>
                       <a href="https://www.gov.br/s/outra/view">menu fora do miolo</a>""",
            f"{pasta}?b_start:int=1": f"""<div id="content-core"><a href="{pasta}/ano-2008/view">2008</a>
                       <a href="{pasta}?b_start:int=0">1</a></div>""",
            f"{pasta}?b_start:int=0": f'<div id="content-core"><a href="{pasta}/ano-2022/view">2022</a></div>',
            f"{pasta}/ano-2022/view": f'<div id="content-core"><a href="{pasta}/ano-2022/@@download/file">BPS_2022.xlsx</a></div>',
            f"{pasta}/ano-2008/view": f'<div id="content-core"><a href="{pasta}/ano-2008/@@download/file">BPS_2008.xls</a></div>',
        }
        tarefas = list(ResolvedorPlone(contexto(http=HttpFalso(paginas))).resolver(pasta, SECAO))
        self.assertEqual([t.destino.name for t in tarefas], ["BPS_2022.xlsx", "BPS_2008.xls"])
        self.assertTrue(all(t.url.endswith("/@@download/file") for t in tarefas))


class TestPlanejamento(unittest.TestCase):
    def test_cadeia_dedup_e_ignoradas(self):
        secoes = [
            Secao(1, "A (DS-01)", ("https://x.org/a.csv", "https://github.com/u/r", "https://desconhecido.org/")),
            Secao(2, "B (DS-02)", ("https://x.org/a.csv",)),
        ]
        with self.assertLogs("coletor.planejamento", "INFO"):
            plano = planejar(secoes, criar_resolvedores(contexto()))
        self.assertEqual([t.url for t in plano.tarefas], ["https://x.org/a.csv"])
        self.assertEqual([url for url, _ in plano.ignoradas], ["https://github.com/u/r", "https://desconhecido.org/"])
        self.assertEqual(plano.falhas, [])

    def test_filtro_de_secoes(self):
        import re
        secoes = [Secao(1, "A (DS-01)", ("https://x.org/a.csv",)), Secao(2, "B (DS-02)", ("https://x.org/b.csv",))]
        with self.assertLogs("coletor.planejamento", "INFO"):
            plano = planejar(secoes, criar_resolvedores(contexto()), re.compile("DS-02"))
        self.assertEqual([t.destino.name for t in plano.tarefas], ["b.csv"])


if __name__ == "__main__":
    unittest.main()
