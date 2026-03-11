import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from fpdf import FPDF
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, date, timedelta
import os

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Bensdorp Weekly Pro", layout="wide")

# --- FUNZIONI DI CALCOLO ---
def calculate_drawdown(equity_series):
    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    return drawdown, drawdown.min()

# --- GENERAZIONE REPORT PDF ---
def generate_pdf(top_picks, spy_status, mdd, total_ret, equity_path, heatmap_path):
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(200, 10, "Report Weekly Rotation - Strategia Bensdorp", ln=True, align='C')
        
        pdf.set_font("Arial", '', 12)
        pdf.ln(5)
        pdf.cell(200, 10, f"Data Report: {datetime.now().strftime('%d/%m/%Y')}", ln=True)
        pdf.cell(200, 10, f"Stato Mercato (SPY): {spy_status}", ln=True)
        pdf.cell(200, 10, f"Rendimento Periodo: {total_ret:.2%}", ln=True)
        pdf.cell(200, 10, f"Max Drawdown: {mdd:.2%}", ln=True)
        
        # Inserimento Immagini con controllo esistenza
        if os.path.exists(equity_path):
            pdf.ln(5)
            pdf.set_font("Arial", 'B', 14)
            pdf.cell(200, 10, "Performance Storica:", ln=True)
            pdf.image(equity_path, x=10, w=180)
        
        if os.path.exists(heatmap_path):
            pdf.add_page()
            pdf.set_font("Arial", 'B', 14)
            pdf.cell(200, 10, "Heatmap Rendimenti Mensili:", ln=True)
            pdf.image(heatmap_path, x=10, w=180)
        
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(200, 10, "Titoli Selezionati:", ln=True)
        pdf.set_font("Arial", '', 10)
        pdf.ln(5)
        for _, row in top_picks.iterrows():
            line = f"- {row['Ticker']}: Prezzo ${row['Price']:.2f} | ROC: {row['ROC']:.2%} | RSI: {row['RSI3']:.1f}"
            pdf.cell(200, 8, line, ln=True)
        
        return pdf.output(dest='S').encode('latin-1')
    except Exception as e:
        st.error(f"Errore generazione PDF: {e}")
        return None

# --- INVIO EMAIL ---
def send_email(recipient_email, pdf_content, spy_status):
    if pdf_content is None:
        return False
    try:
        # Recupero Secrets con fallback per evitare crash
        user = st.secrets.get("EMAIL_USER")
        password = st.secrets.get("EMAIL_PASSWORD")
        
        if not user or not password:
            st.error("Credenziali Email mancanti nei Secrets di Render!")
            return False

        msg = MIMEMultipart()
        msg['From'] = user
        msg['To'] = recipient_email
        msg['Subject'] = f"📊 Report Trading - {spy_status} Market"
        msg.attach(MIMEText("In allegato il report settimanale Bensdorp.", 'plain'))
        
        part = MIMEApplication(pdf_content, Name="Report_Bensdorp.pdf")
        part['Content-Disposition'] = 'attachment; filename="Report_Bensdorp.pdf"'
        msg.attach(part)
        
        # Connessione SMTP sicura
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Errore SMTP: {e}")
        return False

# --- UI SIDEBAR ---
st.sidebar.header("⚙️ Parametri")
spy_buffer = st.sidebar.slider("SPY Buffer %", 0.0, 5.0, 2.0) / 100
rsi_limit = st.sidebar.slider("Soglia RSI(3)", 10, 70, 50)
min_price = st.sidebar.number_input("Prezzo Min $", value=1.0)
min_volume = st.sidebar.number_input("Vol Medio Min", value=1000000)
top_n = st.sidebar.number_input("Target Portafoglio", value=10)

st.sidebar.divider()
start_date = st.sidebar.date_input("Inizio Backtest", value=date.today() - timedelta(days=365*3))
end_date = st.sidebar.date_input("Fine Backtest", value=date.today())

st.sidebar.divider()
email_dest = st.sidebar.text_input("Email Destinatario")
uploaded_file = st.sidebar.file_uploader("Carica Tickers CSV", type=["csv"])

# --- LOGICA ---
if uploaded_file:
    try:
        df_input = pd.read_csv(uploaded_file, sep=None, engine='python')
        df_input.columns = [c.strip().capitalize() for c in df_input.columns]
        tickers = df_input['Ticker'].dropna().astype(str).tolist()
        if "SPY" not in [t.upper() for t in tickers]: tickers.append("SPY")
        
        with st.spinner('Calcolo in corso...'):
            download_start = pd.to_datetime(start_date) - timedelta(days=365)
            data_raw = yf.download(tickers, start=download_start, end=end_date, interval="1d")
            close_data = data_raw['Close']
            vol_data = data_raw['Volume']

        # Filtro Titoli
        metrics = []
        for t in tickers:
            if t.upper() == "SPY" or t not in close_data.columns: continue
            s = close_data[t].dropna()
            v = vol_data[t].dropna()
            if len(s) < 200: continue
            p, sma = s.iloc[-1], s.rolling(200).mean().iloc[-1]
            roc = (p / s.iloc[-200]) - 1
            rsi = 100 - (100 / (1 + (s.diff().where(s.diff() > 0, 0).rolling(3).mean() / ((-s.diff().where(s.diff() < 0, 0)).rolling(3).mean() + 0.001)).iloc[-1]))
            
            if p >= min_price and v.tail(20).mean() >= min_volume and p > sma and rsi < rsi_limit:
                metrics.append({'Ticker': t, 'Price': p, 'ROC': roc, 'RSI3': rsi})
        
        df_res = pd.DataFrame(metrics).sort_values(by='ROC', ascending=False).head(top_n)

        if not df_res.empty:
            st.title("📈 Analisi Strategia")
            
            # Backtest
            bt_prices = close_data[df_res['Ticker'].tolist()].loc[start_date:end_date]
            weekly_ret = bt_prices.resample('W-MON').last().pct_change().mean(axis=1)
            equity = (1 + weekly_ret).cumprod().fillna(1)
            _, mdd = calculate_drawdown(equity)

            # --- GRAFICI ---
            fig_eq, ax_eq = plt.subplots(figsize=(10, 4))
            ax_eq.plot(equity, color='navy')
            ax_eq.set_title("Equity Curve")
            st.pyplot(fig_eq)
            fig_eq.savefig("equity_tmp.png", bbox_inches='tight')
            plt.close(fig_eq) # Libera memoria

            monthly_ret = weekly_ret.resample('ME').apply(lambda x: (1 + x).prod() - 1)
            h_df = monthly_ret.to_frame(name='Ret')
            h_df['Year'], h_df['Month'] = h_df.index.year, h_df.index.month_name()
            pivot = h_df.pivot(index='Year', columns='Month', values='Ret').reindex(columns=['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'])
            
            fig_hm, ax_hm = plt.subplots(figsize=(12, 5))
            sns.heatmap(pivot, annot=True, fmt=".1%", cmap="RdYlGn", center=0, ax=ax_hm)
            st.pyplot(fig_hm)
            fig_hm.savefig("heatmap_tmp.png", bbox_inches='tight')
            plt.close(fig_hm) # Libera memoria

            st.table(df_res.style.format({'Price': '{:.2f}', 'ROC': '{:.2%}', 'RSI3': '{:.1f}'}))

            # --- GENERAZIONE PDF ---
            pdf_data = generate_pdf(df_res, ("BULL" if close_data['SPY'].iloc[-1] > close_data['SPY'].rolling(200).mean().iloc[-1] else "BEAR"), mdd, equity.iloc[-1]-1, "equity_tmp.png", "heatmap_tmp.png")
            
            c_pdf, c_mail = st.columns(2)
            if pdf_data:
                c_pdf.download_button("📥 Scarica PDF", pdf_data, "Report.pdf", "application/pdf")
                
                if c_mail.button("📧 Invia per Email"):
                    if email_dest:
                        with st.spinner('Invio in corso...'):
                            success = send_email(email_dest, pdf_data, "Bensdorp Analysis")
                            if success: st.success("Inviata!")
                    else:
                        st.warning("Manca l'indirizzo email!")

            # Cleanup
            for f in ["equity_tmp.png", "heatmap_tmp.png"]:
                if os.path.exists(f): os.remove(f)

    except Exception as e:
        st.error(f"Errore critico: {e}")
