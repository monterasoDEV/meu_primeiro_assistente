import streamlit as st
import PyPDF2
import json
import os
import re
import io
import uuid
import zlib
import shutil
import pandas as pd
from datetime import datetime, timedelta
from PIL import Image
from google import genai
from google.genai import types

# ==========================================
# 1. Configuração da Página Web
# ==========================================
st.set_page_config(page_title="Tutor CIn - Lucas", page_icon="💻", layout="wide", initial_sidebar_state="expanded")

# --- Estilo customizado (tema "terminal de programador") ---
CSS_CUSTOMIZADO = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&display=swap');

@keyframes revelarResposta {
    from { opacity: 0; transform: translateY(6px) scale(0.98); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}
.resposta-animada { animation: revelarResposta 0.35s ease-out; }

.badge-deck {
    display: inline-block;
    padding: 3px 12px;
    border-radius: 999px;
    color: white;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
}

.conquista-card {
    border-radius: 10px;
    padding: 12px 6px;
    text-align: center;
}
.conquista-card b {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
}
.conquista-card span {
    font-size: 0.68rem;
    color: rgba(148,163,184,0.9);
}
.conquista-on { background: rgba(87, 204, 121, 0.14); border: 1px solid #57cc79; }
.conquista-off { background: rgba(148, 163, 184, 0.06); border: 1px dashed rgba(148,163,184,0.35); opacity: 0.6; }
</style>
"""
st.markdown(CSS_CUSTOMIZADO, unsafe_allow_html=True)

# PREPARAÇÃO PARA HOSPEDAGEM GRATUITA (STREAMLIT CLOUD)
# O código tenta ler a chave dos Secrets (seguro). Se não encontrar (no seu PC local), usa a chave fixa.
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError:
    st.error("⚠️ Chave da API do Gemini não configurada nos Secrets do Streamlit.")
    st.stop()

@st.cache_resource
def get_client():
    return genai.Client(api_key=API_KEY)

client = get_client()

# ==========================================
# 2. Funções Base e Sistema de Arquivos
# ==========================================
FICHEIRO_MEMORIA = "memoria_assistente.json"
FICHEIRO_PONTOS = "pontos_assistente.json"
FICHEIRO_FLASHCARDS = "baralho_anki.json"
FICHEIRO_HISTORICO = "historico_revisoes.json"

PASTA_BACKUPS = "backups"
MAX_BACKUPS = 10

PALETA_CORES_DECK = ["#6C5CE7", "#00B894", "#0984E3", "#E17055", "#FDCB6E", "#E84393", "#00CEC9", "#D63031", "#55A3FF", "#26DE81"]
PALETA_ICONES_DECK = ["📘", "📗", "📙", "📕", "📓", "📔", "📒", "📚", "🧠", "💡"]

# --- Backup automático + gravação atômica ---
def _criar_backup(caminho):
    """Guarda uma cópia datada do arquivo antes de sobrescrevê-lo, mantendo só as MAX_BACKUPS mais recentes."""
    if not os.path.exists(caminho):
        return
    os.makedirs(PASTA_BACKUPS, exist_ok=True)
    nome_base = os.path.basename(caminho)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(PASTA_BACKUPS, f"{timestamp}__{nome_base}")
    try:
        shutil.copy2(caminho, destino)
    except Exception:
        return
    relacionados = sorted(f for f in os.listdir(PASTA_BACKUPS) if f.endswith(f"__{nome_base}"))
    excedente = len(relacionados) - MAX_BACKUPS
    for antigo in relacionados[:max(0, excedente)]:
        try:
            os.remove(os.path.join(PASTA_BACKUPS, antigo))
        except Exception:
            pass

def _backup_mais_recente(caminho):
    if not os.path.isdir(PASTA_BACKUPS):
        return None
    nome_base = os.path.basename(caminho)
    relacionados = sorted(f for f in os.listdir(PASTA_BACKUPS) if f.endswith(f"__{nome_base}"))
    return os.path.join(PASTA_BACKUPS, relacionados[-1]) if relacionados else None

def guardar_json(caminho, dados, versionar=False):
    """Gravação atômica: escreve num .tmp e só troca pelo arquivo real no final,
    então uma queda de energia ou fechamento abrupto do app nunca deixa o arquivo pela metade."""
    if versionar:
        _criar_backup(caminho)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)
    os.replace(tmp, caminho)

def carregar_json(caminho, default):
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            backup = _backup_mais_recente(caminho)
            if backup:
                try:
                    with open(backup, "r", encoding="utf-8") as f:
                        dados = json.load(f)
                    st.session_state.setdefault("avisos_recuperacao", []).append(os.path.basename(caminho))
                    return dados
                except Exception:
                    pass
            st.session_state.setdefault("avisos_recuperacao", []).append(f"{os.path.basename(caminho)} (sem backup disponível)")
            return default
    return default

@st.cache_data
def extrair_texto_pdf(bytes_pdf):
    leitor = PyPDF2.PdfReader(io.BytesIO(bytes_pdf))
    return "".join([pagina.extract_text() + "\n" for pagina in leitor.pages if pagina.extract_text()])

def dar_xp(quantidade, mensagem=""):
    st.session_state.xp += quantidade
    guardar_json(FICHEIRO_PONTOS, {"xp": st.session_state.xp})
    if mensagem:
        st.toast(f"**+{quantidade} XP!** {mensagem}", icon="✨")

def cor_do_deck(nome):
    indice = zlib.crc32(nome.encode("utf-8")) % len(PALETA_CORES_DECK)
    return PALETA_CORES_DECK[indice]

def icone_do_deck(nome):
    indice = zlib.crc32(("icone_" + nome).encode("utf-8")) % len(PALETA_ICONES_DECK)
    return PALETA_ICONES_DECK[indice]

def indice_por_id(id_cartao):
    for i, c in enumerate(st.session_state.flashcards):
        if c.get("id") == id_cartao:
            return i
    return None

def obter_cartoes_fracos(flashcards):
    return [c for c in flashcards if c.get("ease_factor", 2.5) < 2.3 or c.get("repeticoes", 1) == 0]

def calcular_streak(historico):
    if not historico:
        return 0
    dias = sorted(set(h["data"] for h in historico), reverse=True)
    dias_dt = [datetime.strptime(d, "%Y-%m-%d").date() for d in dias]
    hoje = datetime.now().date()
    if dias_dt[0] not in (hoje, hoje - timedelta(days=1)):
        return 0
    streak = 1
    for i in range(1, len(dias_dt)):
        if (dias_dt[i - 1] - dias_dt[i]).days == 1:
            streak += 1
        else:
            break
    return streak

def calcular_retencao(historico, dias=30):
    limite = datetime.now().date() - timedelta(days=dias)
    recentes = [h for h in historico if datetime.strptime(h["data"], "%Y-%m-%d").date() >= limite]
    if not recentes:
        return None
    acertos = len([h for h in recentes if h["qualidade"] >= 3])
    return round((acertos / len(recentes)) * 100)

def contar_revisoes_por_dia(historico, dias=14):
    hoje = datetime.now().date()
    contagem = {(hoje - timedelta(days=i)): 0 for i in range(dias - 1, -1, -1)}
    for h in historico:
        d = datetime.strptime(h["data"], "%Y-%m-%d").date()
        if d in contagem:
            contagem[d] += 1
    return contagem

def gerar_heatmap_html(historico, semanas=13):
    hoje = datetime.now().date()
    contagem = {}
    for h in historico:
        d = datetime.strptime(h["data"], "%Y-%m-%d").date()
        contagem[d] = contagem.get(d, 0) + 1

    inicio = hoje - timedelta(weeks=semanas - 1)
    offset_domingo = (inicio.weekday() + 1) % 7  # weekday(): seg=0 ... dom=6
    inicio -= timedelta(days=offset_domingo)

    def cor_intensidade(q):
        if q == 0: return "rgba(148,163,184,0.15)"
        if q <= 2: return "#a8e6b0"
        if q <= 4: return "#57cc79"
        if q <= 7: return "#28a349"
        return "#0f6b28"

    total_dias = (hoje - inicio).days + 1
    total_semanas = (total_dias // 7) + 1

    colunas_html = ""
    for semana in range(total_semanas):
        coluna = "<div style='display:flex;flex-direction:column;gap:3px;'>"
        for dia_semana in range(7):
            data_atual = inicio + timedelta(weeks=semana, days=dia_semana)
            if data_atual > hoje:
                coluna += "<div style='width:11px;height:11px;'></div>"
                continue
            q = contagem.get(data_atual, 0)
            cor = cor_intensidade(q)
            titulo = f"{data_atual.strftime('%d/%m/%Y')}: {q} revisão(ões)"
            coluna += f"<div title='{titulo}' style='width:11px;height:11px;border-radius:3px;background:{cor};'></div>"
        coluna += "</div>"
        colunas_html += coluna

    return f"<div style='display:flex;gap:3px;overflow-x:auto;padding:6px 0;'>{colunas_html}</div>"

def obter_conquistas(historico, streak, xp):
    total = len(historico)
    nivel_atual = (xp // 100) + 1
    return [
        {"nome": "Primeiro Boot", "desc": "1ª revisão feita", "emoji": "🎯", "conquistado": total >= 1},
        {"nome": "Compilando Hábito", "desc": "3 dias seguidos", "emoji": "🔥", "conquistado": streak >= 3},
        {"nome": "Uptime 7 Dias", "desc": "7 dias seguidos", "emoji": "🚀", "conquistado": streak >= 7},
        {"nome": "50 Revisões", "desc": "50 cartões no total", "emoji": "📚", "conquistado": total >= 50},
        {"nome": "100 Revisões", "desc": "100 cartões no total", "emoji": "💯", "conquistado": total >= 100},
        {"nome": "Dev Nível 5+", "desc": "Alcançou o nível 5", "emoji": "👨‍💻", "conquistado": nivel_atual >= 5},
    ]

# ==========================================
# 3. Inicialização de Estado
# ==========================================
if "chat_history" not in st.session_state: st.session_state.chat_history = carregar_json(FICHEIRO_MEMORIA, [])
if "xp" not in st.session_state: st.session_state.xp = carregar_json(FICHEIRO_PONTOS, {"xp": 0}).get("xp", 0)
if "flashcards" not in st.session_state:
    st.session_state.flashcards = carregar_json(FICHEIRO_FLASHCARDS, [])
    _precisa_salvar = False
    for _c in st.session_state.flashcards:
        if "id" not in _c:
            _c["id"] = str(uuid.uuid4())[:8]
            _precisa_salvar = True
    if _precisa_salvar:
        guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards)
if "cartao_atual_verso" not in st.session_state: st.session_state.cartao_atual_verso = False
if "historico" not in st.session_state: st.session_state.historico = carregar_json(FICHEIRO_HISTORICO, [])

if st.session_state.get("avisos_recuperacao"):
    for aviso in st.session_state.avisos_recuperacao:
        st.warning(f"⚠️ '{aviso}' estava corrompido ou ilegível — recuperado a partir do backup mais recente disponível.", icon="🛠️")
    st.session_state.avisos_recuperacao = []

nivel = (st.session_state.xp // 100) + 1
xp_progresso = st.session_state.xp % 100

# ==========================================
# 4. Algoritmo SM-2 (Revisão Espaçada)
# ==========================================
def avaliar_cartao(cartao, qualidade):
    repeticoes, ef, intervalo = cartao.get("repeticoes", 0), cartao.get("ease_factor", 2.5), cartao.get("intervalo", 0)
    if qualidade >= 3:
        if repeticoes == 0: intervalo = 1
        elif repeticoes == 1: intervalo = 6
        else: intervalo = round(intervalo * ef)
        repeticoes += 1
    else:
        repeticoes, intervalo = 0, 1

    ef = max(1.3, ef + (0.1 - (5 - qualidade) * (0.08 + (5 - qualidade) * 0.02)))

    cartao["repeticoes"], cartao["ease_factor"], cartao["intervalo"] = repeticoes, round(ef, 2), intervalo
    cartao["proxima_revisao"] = (datetime.now() + timedelta(days=intervalo)).strftime("%Y-%m-%d")
    return cartao

# ==========================================
# 5. Barra Lateral (Painel de Controlo)
# ==========================================
with st.sidebar:
    st.title("💻 CIn Tutor Workspace")

    with st.container(border=True):
        st.markdown(f"### 👨‍💻 Dev Nível {nivel}")
        st.progress(xp_progresso / 100, text=f"XP: {st.session_state.xp} / {nivel * 100}")
        st.caption(f"🔥 Streak de estudo: {calcular_streak(st.session_state.historico)} dia(s)")

    modo_app = st.radio("📍 Módulos do Sistema", ["💬 Terminal CIn (Chat)", "🗂️ Memória RAM (Anki)"], label_visibility="collapsed")
    st.divider()

    aba_arquivos, aba_acoes, aba_config = st.tabs(["📁 Dados", "⚡ Ações", "⚙️ Config"])

    with aba_arquivos:
        ficheiro_pdf = st.file_uploader("Upload de Slides/PDF", type=["pdf"])
        contexto_pdf = extrair_texto_pdf(ficheiro_pdf.getvalue()) if ficheiro_pdf else ""
        ficheiro_imagem = st.file_uploader("Foto do Quadro/Código", type=["png", "jpg", "jpeg"])
        imagem_pil = Image.open(ficheiro_imagem) if ficheiro_imagem else None

    with aba_acoes:
        st.subheader("Gerador de Flashcards")
        nome_baralho = st.text_input("Disciplina (Ex: Matemática Discreta)", value="Programação")
        usar_lacunas = st.checkbox("Código/Lacunas (Cloze)")
        gerar_inversos = st.checkbox("Bidirecionais")
        btn_gerar_baralho = st.button("🤖 Extrair Flashcards", type="primary", use_container_width=True)

        st.divider()
        st.subheader("Modos de Estudo")
        btn_codigo = st.button("💻 Explicar Código Passo a Passo", use_container_width=True)
        btn_reforco = st.button("🚨 Reforço de Erros (Weakness)", use_container_width=True)

    with aba_config:
        usar_pesquisa = st.toggle("🌐 Pesquisa Web (Grounding)", value=False)
        if st.button("🗑️ Limpar Console (Reset Chat)"):
            st.session_state.chat_history = []
            guardar_json(FICHEIRO_MEMORIA, [])
            st.toast("Console limpo com sucesso!", icon="🧹")
            st.rerun()

        st.divider()
        st.caption("💾 Backup completo (cartões + histórico + XP + chat)")
        _backup_completo = {
            "exportado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "flashcards": st.session_state.flashcards,
            "historico": st.session_state.historico,
            "xp": st.session_state.xp,
            "chat_history": st.session_state.chat_history,
        }
        st.download_button(
            "📥 Baixar backup (.json)",
            data=json.dumps(_backup_completo, ensure_ascii=False, indent=2),
            file_name=f"backup_tutorcin_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            use_container_width=True,
        )

        ficheiro_restauro = st.file_uploader("Restaurar backup (.json)", type=["json"], key="upload_restauro")
        if ficheiro_restauro:
            confirmar_restauro = st.checkbox("Entendo que isso substitui os dados atuais.")
            if st.button("♻️ Restaurar agora", disabled=not confirmar_restauro, use_container_width=True):
                try:
                    dados_restaurados = json.load(ficheiro_restauro)
                    st.session_state.flashcards = dados_restaurados.get("flashcards", [])
                    st.session_state.historico = dados_restaurados.get("historico", [])
                    st.session_state.xp = dados_restaurados.get("xp", 0)
                    st.session_state.chat_history = dados_restaurados.get("chat_history", [])
                    guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                    guardar_json(FICHEIRO_HISTORICO, st.session_state.historico, versionar=True)
                    guardar_json(FICHEIRO_PONTOS, {"xp": st.session_state.xp})
                    guardar_json(FICHEIRO_MEMORIA, st.session_state.chat_history)
                    st.toast("Backup restaurado com sucesso!", icon="♻️")
                    st.rerun()
                except Exception as e:
                    st.error(f"Arquivo inválido: {e}")

# ==========================================
# 6. Configuração do Gemini (CÉREBRO UFPE)
# ==========================================
instrucao_base = """Você é um tutor de excelência em Ciência da Computação. 
O aluno chama-se Lucas, está no 1º período de Ciência da Computação no CIn da UFPE.
Regras:
1. NUNCA dê o código ou a resposta pronta matematicamente. Guie-o na lógica.
2. Use analogias de computação.
3. Foque em eficiência (Big-O), boas práticas e fundamentos fortes."""

instrucao_sistema = instrucao_base + f"\n\nMATERIAL DE APOIO:\n{contexto_pdf[:20000]}" if contexto_pdf else instrucao_base
historico_gemini = [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part.from_text(text=m["content"])]) for m in st.session_state.chat_history]

if ("pdf_atual" not in st.session_state or st.session_state.pdf_atual != ficheiro_pdf or st.session_state.get("pesquisa_ativa") != usar_pesquisa):
    st.session_state.pdf_atual, st.session_state.pesquisa_ativa = ficheiro_pdf, usar_pesquisa
    ferramentas = [{'google_search': {}}] if usar_pesquisa else None
    config = types.GenerateContentConfig(system_instruction=instrucao_sistema, temperature=0.7, tools=ferramentas)
    st.session_state.gemini_chat = client.chats.create(model="gemini-3.1-flash-lite", config=config, history=historico_gemini)

# ==========================================
# 7. MÓDULO 1: TERMINAL CIN (CHAT)
# ==========================================
if modo_app == "💬 Terminal CIn (Chat)":
    st.title("👨‍💻 Terminal de Estudos CIn/UFPE")
    st.caption("Conectado ao modelo Gemini. Pronto para compilar ideias.")
    st.divider()

    for message in st.session_state.chat_history:
        avatar = "👨‍💻" if message["role"] == "user" else "🤖"
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(message["content"])

    prompt_enviado = None

    if btn_codigo:
        prompt_enviado = "Analisa este material/código e explica a lógica de execução passo a passo."

    elif btn_reforco:
        cartoes_fracos = obter_cartoes_fracos(st.session_state.flashcards)
        if not cartoes_fracos:
            st.toast("O seu desempenho está excelente! Não há falhas críticas registadas.", icon="🏆")
        else:
            conceitos_fracos = [c['frente'] for c in cartoes_fracos[:5]]
            prompt_enviado = f"Estou com muita dificuldade nestes conceitos cruciais: {conceitos_fracos}. Gera um mini-teste prático e focado para me forçar a aplicar estes conceitos. Faz perguntas desafiadoras (nível CIn/UFPE) e não me dês a resposta até eu tentar resolver."

    elif btn_gerar_baralho:
        tipo_prompt = "cartões de Preenchimento de Lacunas (Cloze)." if usar_lacunas else "flashcards normais."
        prompt_enviado = (
            f"Extraia 5 conceitos cruciais do material. Gere {tipo_prompt}\n"
            'RETORNE ESTRITAMENTE UM ARRAY JSON VÁLIDO (aspas duplas, sem texto extra antes ou depois) '
            'NESTE FORMATO: [{"frente": "...", "verso": "..."}]'
        )

    prompt_digitado = st.chat_input("Insira o comando ou dúvida (Ex: O que é recursão?)...")
    if prompt_digitado: prompt_enviado = prompt_digitado

    if prompt_enviado:
        with st.chat_message("user", avatar="👨‍💻"): st.markdown(prompt_enviado)
        st.session_state.chat_history.append({"role": "user", "content": prompt_enviado})

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("A processar e a compilar dados... ⚙️"):
                try:
                    conteudo = [imagem_pil, prompt_enviado] if imagem_pil else prompt_enviado
                    resposta = st.session_state.gemini_chat.send_message(conteudo)

                    if btn_gerar_baralho:
                        match = re.search(r'\[\s*\{.*?\}\s*\]', resposta.text, re.DOTALL)
                        if match:
                            novos_cartoes = json.loads(match.group(0))
                            hoje = datetime.now().strftime("%Y-%m-%d")
                            for c in novos_cartoes:
                                st.session_state.flashcards.append({"id": str(uuid.uuid4())[:8], "frente": c["frente"], "verso": c["verso"], "deck": nome_baralho, "repeticoes": 0, "ease_factor": 2.5, "intervalo": 0, "proxima_revisao": hoje})
                                if gerar_inversos and not usar_lacunas:
                                    st.session_state.flashcards.append({"id": str(uuid.uuid4())[:8], "frente": c["verso"], "verso": c["frente"], "deck": nome_baralho, "repeticoes": 0, "ease_factor": 2.5, "intervalo": 0, "proxima_revisao": hoje})
                            guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                            st.success(f"**Sucesso!** Cartões inseridos na BD do baralho '{nome_baralho}'.")
                        else:
                            st.error("Falha ao gerar o JSON da base de dados.")
                    else:
                        st.markdown(resposta.text)
                        st.session_state.chat_history.append({"role": "assistant", "content": resposta.text})
                        guardar_json(FICHEIRO_MEMORIA, st.session_state.chat_history)

                    dar_xp(10, "Estudo Concluído!")
                except Exception as e: st.error(f"Erro de Execução: {e}")

# ==========================================
# 8. MÓDULO 2: MEMÓRIA RAM (ANKI) - UI DINÂMICA
# ==========================================
elif modo_app == "🗂️ Memória RAM (Anki)":
    st.title("🗂️ Base de Dados de Conhecimento (SRS)")

    hoje_str = datetime.now().strftime("%Y-%m-%d")
    streak_atual = calcular_streak(st.session_state.historico)

    tab_revisar, tab_gerenciar, tab_stats = st.tabs(["🎯 Revisar", "🗃️ Gerenciar Cartões", "📊 Estatísticas"])

    # ---------- TAB: REVISAR ----------
    with tab_revisar:
        decks_existentes = sorted(set(c.get("deck", "Geral") for c in st.session_state.flashcards))
        col_filtro, col_streak = st.columns([2, 1])
        with col_filtro:
            deck_selecionado = st.selectbox("📂 Deck", ["Todos"] + decks_existentes, key="deck_revisar")
        with col_streak:
            st.metric("🔥 Streak", f"{streak_atual} dia(s)")

        # troca de deck limpa a resposta revelada do cartão anterior
        if st.session_state.get("_ultimo_deck_revisar") != deck_selecionado:
            st.session_state.cartao_atual_verso = False
            st.session_state["_ultimo_deck_revisar"] = deck_selecionado

        cartoes_filtrados = [c for c in st.session_state.flashcards if deck_selecionado == "Todos" or c.get("deck", "Geral") == deck_selecionado]
        novos = len([c for c in cartoes_filtrados if c.get("repeticoes", 0) == 0])
        pendentes_hoje = [c for c in cartoes_filtrados if c.get("proxima_revisao", hoje_str) <= hoje_str]
        if deck_selecionado == "Todos":
            revisados_hoje = len([h for h in st.session_state.historico if h["data"] == hoje_str])
        else:
            revisados_hoje = len([h for h in st.session_state.historico if h["data"] == hoje_str and h.get("deck") == deck_selecionado])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🆕 Novos", novos)
        c2.metric("⏰ Pendentes", len(pendentes_hoje))
        c3.metric("✅ Revisados hoje", revisados_hoje)
        c4.metric("📦 Total no deck", len(cartoes_filtrados))

        total_ciclo = revisados_hoje + len(pendentes_hoje)
        if total_ciclo > 0:
            st.progress(revisados_hoje / total_ciclo, text=f"Sessão de hoje: {revisados_hoje}/{total_ciclo} cartões")

        st.markdown("---")

        if not cartoes_filtrados:
            st.info("Nenhum cartão neste deck ainda. Gere alguns no Terminal CIn ou adicione manualmente na aba **Gerenciar Cartões**.")
        elif not pendentes_hoje:
            st.success(f"🎉 **Sem dependências circulares!** O seu estudo de '{deck_selecionado}' está em dia.")
        else:
            cartao_atual = pendentes_hoje[0]
            deck_nome = cartao_atual.get("deck", "Geral")
            cor = cor_do_deck(deck_nome)
            icone = icone_do_deck(deck_nome)

            with st.container(border=True):
                st.markdown(f"<span class='badge-deck' style='background:{cor};'>{icone} {deck_nome}</span>", unsafe_allow_html=True)
                st.markdown("### ❓ Input (Pergunta)")
                st.info(cartao_atual["frente"])

                if not st.session_state.cartao_atual_verso:
                    st.divider()
                    if st.button("👁️ Executar Script (Revelar Resposta)", use_container_width=True, type="primary"):
                        st.session_state.cartao_atual_verso = True
                        st.rerun()
                else:
                    st.divider()
                    st.markdown("<div class='resposta-animada'>", unsafe_allow_html=True)
                    st.markdown("### 💡 Output (Resposta)")
                    st.success(cartao_atual["verso"])
                    st.markdown("</div>", unsafe_allow_html=True)

                    st.markdown("#### ⚙️ Como avalia o seu tempo de processamento?")
                    col1, col2, col3, col4 = st.columns(4)

                    def atualizar_e_avancar(qualidade, cor_toast):
                        idx = indice_por_id(cartao_atual["id"])
                        st.session_state.flashcards[idx] = avaliar_cartao(cartao_atual, qualidade)
                        guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)

                        st.session_state.historico.append({"data": hoje_str, "deck": deck_nome, "qualidade": qualidade})
                        guardar_json(FICHEIRO_HISTORICO, st.session_state.historico, versionar=True)

                        st.session_state.cartao_atual_verso = False
                        dar_xp(8, f"Cartão Processado! ({cor_toast})")

                        if len(pendentes_hoje) == 1:
                            st.balloons()
                        st.rerun()

                    if col1.button("❌ Exception (Errei)\nRever hoje", use_container_width=True): atualizar_e_avancar(0, "Vermelho")
                    if col2.button("😅 O(n²) (Difícil)\nMenos dias", use_container_width=True): atualizar_e_avancar(3, "Laranja")
                    if col3.button("👍 O(n) (Bom)\nNormal", use_container_width=True): atualizar_e_avancar(4, "Verde")
                    if col4.button("🚀 O(1) (Fácil)\nMais dias", use_container_width=True): atualizar_e_avancar(5, "Azul")

    # ---------- TAB: GERENCIAR ----------
    with tab_gerenciar:
        st.subheader("🗃️ Gerenciar Cartões")

        with st.expander("➕ Adicionar cartão manualmente"):
            with st.form("form_novo_cartao", clear_on_submit=True):
                novo_deck = st.text_input("Disciplina/Deck", value="Geral")
                nova_frente = st.text_area("Pergunta (frente)")
                novo_verso = st.text_area("Resposta (verso)")
                enviado = st.form_submit_button("💾 Salvar Cartão", type="primary")
                if enviado:
                    if nova_frente and novo_verso:
                        st.session_state.flashcards.append({
                            "id": str(uuid.uuid4())[:8],
                            "frente": nova_frente, "verso": novo_verso, "deck": novo_deck or "Geral",
                            "repeticoes": 0, "ease_factor": 2.5, "intervalo": 0,
                            "proxima_revisao": hoje_str
                        })
                        guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                        st.toast("Cartão adicionado!", icon="✅")
                        st.rerun()
                    else:
                        st.warning("Preencha pergunta e resposta antes de salvar.")

        with st.expander("📤 Importar cartões de um CSV"):
            st.caption("O CSV precisa das colunas `frente` e `verso` (a coluna `deck` é opcional).")
            csv_importado = st.file_uploader("Selecionar CSV", type=["csv"], key="upload_csv_cartoes")
            if csv_importado:
                try:
                    df_import = pd.read_csv(csv_importado)
                    colunas = {c.lower(): c for c in df_import.columns}
                    if "frente" not in colunas or "verso" not in colunas:
                        st.error("O CSV precisa ter pelo menos as colunas 'frente' e 'verso'.")
                    else:
                        st.dataframe(df_import.head(5), use_container_width=True)
                        deck_padrao_import = st.text_input("Deck para linhas sem essa coluna", value="Importado")
                        if st.button("✅ Confirmar importação", use_container_width=True):
                            novos_n = 0
                            for _, linha in df_import.iterrows():
                                frente_i = str(linha[colunas["frente"]]).strip()
                                verso_i = str(linha[colunas["verso"]]).strip()
                                if not frente_i or not verso_i or frente_i.lower() == "nan" or verso_i.lower() == "nan":
                                    continue
                                if "deck" in colunas and pd.notna(linha[colunas["deck"]]):
                                    deck_i = str(linha[colunas["deck"]]).strip()
                                else:
                                    deck_i = deck_padrao_import
                                st.session_state.flashcards.append({
                                    "id": str(uuid.uuid4())[:8],
                                    "frente": frente_i, "verso": verso_i, "deck": deck_i or "Importado",
                                    "repeticoes": 0, "ease_factor": 2.5, "intervalo": 0,
                                    "proxima_revisao": hoje_str
                                })
                                novos_n += 1
                            guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                            st.toast(f"{novos_n} cartão(ões) importado(s)!", icon="📤")
                            st.rerun()
                except Exception as e:
                    st.error(f"Não consegui ler esse CSV: {e}")

        with st.expander("🛠️ Ferramentas de Deck (renomear, mesclar ou excluir em massa)"):
            decks_atuais = sorted(set(c.get("deck", "Geral") for c in st.session_state.flashcards))
            if not decks_atuais:
                st.caption("Nenhum deck ainda.")
            else:
                deck_alvo = st.selectbox("Deck", decks_atuais, key="deck_ferramenta")
                qtd_no_deck = len([c for c in st.session_state.flashcards if c.get("deck", "Geral") == deck_alvo])
                st.caption(f"{qtd_no_deck} cartão(ões) neste deck")

                novo_nome_deck = st.text_input("Renomear/mesclar para:", value=deck_alvo, key="novo_nome_deck")
                col_renomear, col_excluir_deck = st.columns(2)
                if col_renomear.button("✏️ Aplicar novo nome", use_container_width=True):
                    if novo_nome_deck.strip():
                        for c in st.session_state.flashcards:
                            if c.get("deck", "Geral") == deck_alvo:
                                c["deck"] = novo_nome_deck.strip()
                        guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                        for _chave in ("deck_gerenciar", "deck_ferramenta", "deck_revisar"):
                            st.session_state.pop(_chave, None)
                        st.toast(f"Deck atualizado para '{novo_nome_deck.strip()}'.", icon="✏️")
                        st.rerun()

                chave_confirma_deck = f"confirma_exclusao_deck_{deck_alvo}"
                if not st.session_state.get(chave_confirma_deck):
                    if col_excluir_deck.button("🗑️ Excluir deck inteiro", use_container_width=True):
                        st.session_state[chave_confirma_deck] = True
                        st.rerun()
                else:
                    st.warning(f"Isso vai excluir os {qtd_no_deck} cartões de '{deck_alvo}'. Confirma?")
                    col_cd1, col_cd2 = st.columns(2)
                    if col_cd1.button("✅ Sim, excluir deck", key="conf_sim_deck", use_container_width=True):
                        st.session_state.flashcards = [c for c in st.session_state.flashcards if c.get("deck", "Geral") != deck_alvo]
                        guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                        st.session_state.pop(chave_confirma_deck, None)
                        for _chave in ("deck_gerenciar", "deck_ferramenta", "deck_revisar"):
                            st.session_state.pop(_chave, None)
                        st.toast("Deck excluído.", icon="🗑️")
                        st.rerun()
                    if col_cd2.button("↩️ Cancelar", key="conf_nao_deck", use_container_width=True):
                        st.session_state.pop(chave_confirma_deck, None)
                        st.rerun()

        col_busca, col_filtro_deck = st.columns([2, 1])
        with col_busca:
            busca = st.text_input("🔍 Buscar por palavra-chave")
        with col_filtro_deck:
            decks_existentes_g = sorted(set(c.get("deck", "Geral") for c in st.session_state.flashcards))
            filtro_deck_g = st.selectbox("Filtrar por deck", ["Todos"] + decks_existentes_g, key="deck_gerenciar")

        resultado = st.session_state.flashcards
        if filtro_deck_g != "Todos":
            resultado = [c for c in resultado if c.get("deck", "Geral") == filtro_deck_g]
        if busca:
            termo = busca.lower()
            resultado = [c for c in resultado if termo in c["frente"].lower() or termo in c["verso"].lower()]

        # filtro mudou -> volta pra página 1
        chave_filtro_atual = (busca, filtro_deck_g)
        if st.session_state.get("_ultimo_filtro_gerenciar") != chave_filtro_atual:
            st.session_state.pagina_gerenciar = 1
            st.session_state["_ultimo_filtro_gerenciar"] = chave_filtro_atual

        col_cap, col_export = st.columns([2, 1])
        col_cap.caption(f"{len(resultado)} cartão(ões) encontrado(s)")
        if resultado:
            csv_export = pd.DataFrame(resultado)[["deck", "frente", "verso"]].to_csv(index=False)
            col_export.download_button("📤 Exportar filtrados (.csv)", data=csv_export, file_name="flashcards_export.csv", mime="text/csv", use_container_width=True)

        CARTOES_POR_PAGINA = 8
        if "pagina_gerenciar" not in st.session_state:
            st.session_state.pagina_gerenciar = 1
        total_paginas = ((len(resultado) - 1) // CARTOES_POR_PAGINA + 1) if resultado else 1
        st.session_state.pagina_gerenciar = max(1, min(st.session_state.pagina_gerenciar, total_paginas))

        if total_paginas > 1:
            col_pag_a, col_pag_b, col_pag_c = st.columns([1, 2, 1])
            with col_pag_a:
                if st.button("⬅️ Anterior", disabled=st.session_state.pagina_gerenciar <= 1, use_container_width=True):
                    st.session_state.pagina_gerenciar -= 1
                    st.rerun()
            with col_pag_b:
                st.markdown(f"<div style='text-align:center;padding-top:8px;'>Página {st.session_state.pagina_gerenciar} de {total_paginas}</div>", unsafe_allow_html=True)
            with col_pag_c:
                if st.button("Próxima ➡️", disabled=st.session_state.pagina_gerenciar >= total_paginas, use_container_width=True):
                    st.session_state.pagina_gerenciar += 1
                    st.rerun()

        inicio_pag = (st.session_state.pagina_gerenciar - 1) * CARTOES_POR_PAGINA
        pagina_atual = resultado[inicio_pag: inicio_pag + CARTOES_POR_PAGINA]

        for cartao in pagina_atual:
            cid = cartao["id"]
            deck_nome_c = cartao.get("deck", "Geral")
            cor_c = cor_do_deck(deck_nome_c)
            with st.expander(f"{icone_do_deck(deck_nome_c)} {cartao['frente'][:60]}"):
                st.markdown(f"<span class='badge-deck' style='background:{cor_c};'>{deck_nome_c}</span>", unsafe_allow_html=True)
                nova_f = st.text_area("Pergunta", value=cartao["frente"], key=f"frente_{cid}")
                novo_v = st.text_area("Resposta", value=cartao["verso"], key=f"verso_{cid}")

                col_salvar, col_excluir = st.columns(2)
                if col_salvar.button("💾 Salvar alterações", key=f"salvar_{cid}", use_container_width=True):
                    idx = indice_por_id(cid)
                    st.session_state.flashcards[idx]["frente"] = nova_f
                    st.session_state.flashcards[idx]["verso"] = novo_v
                    guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                    st.toast("Cartão atualizado!", icon="✏️")
                    st.rerun()

                chave_confirma = f"confirma_exclusao_{cid}"
                if not st.session_state.get(chave_confirma):
                    if col_excluir.button("🗑️ Excluir cartão", key=f"excluir_{cid}", use_container_width=True):
                        st.session_state[chave_confirma] = True
                        st.rerun()
                else:
                    st.warning("Tem certeza que quer excluir este cartão? Essa ação não pode ser desfeita.")
                    col_conf1, col_conf2 = st.columns(2)
                    if col_conf1.button("✅ Sim, excluir", key=f"confirma_sim_{cid}", use_container_width=True):
                        idx = indice_por_id(cid)
                        if idx is not None:
                            st.session_state.flashcards.pop(idx)
                            guardar_json(FICHEIRO_FLASHCARDS, st.session_state.flashcards, versionar=True)
                        st.session_state.pop(chave_confirma, None)
                        st.toast("Cartão excluído.", icon="🗑️")
                        st.rerun()
                    if col_conf2.button("↩️ Cancelar", key=f"confirma_nao_{cid}", use_container_width=True):
                        st.session_state.pop(chave_confirma, None)
                        st.rerun()

    # ---------- TAB: ESTATÍSTICAS ----------
    with tab_stats:
        st.subheader("📊 Estatísticas de Estudo")

        retencao = calcular_retencao(st.session_state.historico)
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("🔥 Streak atual", f"{streak_atual} dia(s)")
        col_s2.metric("📈 Retenção (30 dias)", f"{retencao}%" if retencao is not None else "—")
        col_s3.metric("🗂️ Total de revisões", len(st.session_state.historico))

        st.markdown("#### 🗓️ Mapa de atividade (últimas ~13 semanas)")
        if st.session_state.historico:
            st.markdown(gerar_heatmap_html(st.session_state.historico), unsafe_allow_html=True)
        else:
            st.caption("Ainda sem revisões registadas.")

        st.markdown("#### 📊 Revisões por dia (últimos 14 dias)")
        contagem = contar_revisoes_por_dia(st.session_state.historico, dias=14)
        if any(contagem.values()):
            df_contagem = pd.DataFrame({"Revisões": list(contagem.values())}, index=[d.strftime("%d/%m") for d in contagem.keys()])
            st.bar_chart(df_contagem)
        else:
            st.caption("Ainda sem dados suficientes para o gráfico.")

        st.markdown("#### 🏆 Conquistas")
        conquistas = obter_conquistas(st.session_state.historico, streak_atual, st.session_state.xp)
        cols_conquistas = st.columns(len(conquistas))
        for col, conquista in zip(cols_conquistas, conquistas):
            classe = "conquista-on" if conquista["conquistado"] else "conquista-off"
            col.markdown(
                f"<div class='conquista-card {classe}'>"
                f"<div style='font-size:1.4rem;'>{conquista['emoji']}</div>"
                f"<b>{conquista['nome']}</b><br><span>{conquista['desc']}</span>"
                f"</div>", unsafe_allow_html=True
            )

        cartoes_fracos = obter_cartoes_fracos(st.session_state.flashcards)
        if cartoes_fracos:
            st.markdown("#### ⚠️ Cartões em dificuldade")
            for c in cartoes_fracos[:10]:
                st.markdown(f"- **{c.get('deck', 'Geral')}** — {c['frente'][:80]}")