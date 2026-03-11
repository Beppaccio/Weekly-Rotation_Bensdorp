import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from fpdf import FPDF
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Bensdorp Weekly Pro", layout="wide")

# --- FUNZIONI DI CALCOLO E PDF ---
def calculate_drawdown(equity_series):
    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    return drawdown, drawdown.min()

def generate_pdf(top_picks, spy_status, mdd, total_ret):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, "Report Weekly Rotation - Strategia Bensdorp", ln=True, align='C')
    pdf.set_font("Arial", '', 12)
    pdf.ln(10)
    pdf.cell(200, 10, f"Data Report: {datetime.now().strftime('%d/%m/%Y')}", ln=True)
    pdf.cell(200, 10, f"Stato Mercato (SPY): {spy_status}", ln=True)
    pdf.cell(200, 10, f"Rendimento Backtest: {total_ret:.2%}", ln=True)
    pdf.cell(200, 10, f"Max Drawdown: {mdd:.2%}", ln=True)
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 10, "Titoli Selezionati (Top ROC):", ln=True)
    pdf.set_font("Arial", '', 10)
    for _, row in top_picks.iterrows():
        line = f"- {row['Ticker']}: Prezzo ${row['Price']:.2f} | ROC: {row['ROC']:.2%} | RSI: {row['RSI3']:.1f}"
        pdf.cell(200, 8, line, ln=True)
    return pdf.output(dest='S').encode('latin-1')

def send_email(recipient_email, pdf_content, spy_status):
    # Questi valori andrebbero impostati nei "Secrets" di Render o Hostinger
    sender_email = st.secrets["EMAIL_USER"]
    sender_password = st.secrets["EMAIL_PASSWORD"]
    smtp_server = "smtp.gmail.com" # O il server di Hostinger/SendGrid
    smtp_port = 587

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = f"📊 Report Trading Settimanale - Regime {spy_status}"

    body = "In allegato trovi il report settimanale generato dalla tua app Bensdorp Weekly Rotation."
    msg.attach(MIMEText(body, 'plain'))

    part = MIMEApplication(pdf_content, Name=f"Report_{datetime.now().strftime('%Y%m%d')}.pdf")
    part['Content-Disposition'] = 'attachment; filename="%s"' % f"Report_{datetime.now().strftime('%Y%m%d')}.pdf"
    msg.attach(part)

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Errore invio email: {e}")
        return False

# --- UI SIDEBAR ---
st.sidebar.header("⚙️ Parametri Strategia")
spy_buffer = st.sidebar.slider("SPY SMA200 Buffer (%)", 0.0, 5.0, 2.0) / 100
rsi_limit = st.sidebar.slider("Soglia RSI(3)", 10, 70, 50)
top_n = st.sidebar.number_input("Titoli in Portafoglio", value=10)

st.sidebar.divider()
email_dest = st.sidebar.text_input("Email per notifiche")
uploaded_file = st.sidebar.file_uploader("Carica CSV (Colonna 'Ticker')", type=["csv"])

# --- LOGICA PRINCIPALE ---
if uploaded_file:
    tickers = pd.read_csv(uploaded_file)['Ticker'].tolist()
    if "SPY" not in tickers: tickers.append("SPY")
    
    with st.spinner('Elaborazione dati in corso...'):
        data = yf.download(tickers, period="2y", interval="1d")['Close']
        spy_p, spy_sma = data['SPY'].iloc[-1], data['SPY'].rolling(200).mean().iloc[-1]
        market_bull = spy_p > (spy_sma * (1 + spy_buffer))
        spy_status = "BULL" if market_bull else "BEAR"

        metrics = []
        for t in tickers:
            if t == "SPY": continue
            s = data[t].dropna()
            if len(s) < 200: continue
            p, sma, roc = s.iloc[-1], s.rolling(200).mean().iloc[-1], (s.iloc[-1] / s.iloc[-200]) - 1
            delta = s.diff()
            gain, loss = delta.where(delta > 0, 0).rolling(3).mean(), (-delta.where(delta < 0, 0)).rolling(3).mean()
            rsi = 100 - (100 / (1 + (gain / (loss + 0.001)).iloc[-1]))
            if p > sma and rsi < rsi_limit:
                metrics.append({'Ticker': t, 'Price': p, 'ROC': roc, 'RSI3': rsi})
        
        df_res = pd.DataFrame(metrics).sort_values(by='ROC', ascending=False).head(top_n)

    st.title("📊 Weekly Rotation Dashboard")
    
    # Backtest
    portfolio_prices = data[df_res['Ticker'].tolist()].resample('W-MON').last().pct_change().mean(axis=1)
    equity_curve = (1 + portfolio_prices).cumprod().fillna(1)
    dd_series, mdd = calculate_drawdown(equity_curve)
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Regime Mercato", spy_status)
    c2.metric("Titoli Trovati", len(metrics))
    c3.metric("Max Drawdown", f"{mdd:.2%}")

    st.subheader("🚀 Segnali Attuali")
    st.table(df_res.style.format({'Price': '{:.2f}', 'ROC': '{:.2%}', 'RSI3': '{:.1f}'}))
    st.line_chart(equity_curve)

    # --- AZIONI REPORT ---
    pdf_bytes = generate_pdf(df_res, spy_status, mdd, equity_curve.iloc[-1]-1)
    
    col_pdf, col_mail = st.columns(2)
    col_pdf.download_button("📥 Scarica Report PDF", pdf_bytes, f"Report_{datetime.now().strftime('%Y%m%d')}.pdf", "application/pdf")
    
    if col_mail.button("📧 Invia Report via Email"):
        if email_dest:
            if send_email(email_dest, pdf_bytes, spy_status):
                st.success("Email inviata correttamente!")
        else:
            st.warning("Inserisci un indirizzo email nella sidebar.")
else:
    st.warning("Carica un file CSV per iniziare.")
