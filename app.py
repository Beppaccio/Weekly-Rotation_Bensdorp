import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from fpdf import FPDF
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, date

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
    pdf.cell(200, 10, f"Rendimento Periodo: {total_ret:.2%}", ln=True)
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
    try:
        sender_email = st.secrets["EMAIL_USER"]
        sender_password = st.secrets["EMAIL_PASSWORD"]
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = f"📊 Report Trading Settimanale - Regime {spy_status}"
        msg.attach(MIMEText("In allegato il report settimanale.", 'plain'))
        part = MIMEApplication(pdf_content, Name=f"Report_{datetime.now().strftime('%Y%m%d')}.pdf")
        part['Content-Disposition'] = 'attachment; filename="%s"' % f"Report_{datetime.now().strftime('%Y%m%d')}.pdf"
        msg.attach(part)
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Errore Email: {e}")
        return False

# --- UI SIDEBAR ---
st.sidebar.header("⚙️ Parametri Strategia")
spy_buffer = st.sidebar.slider("SPY SMA200 Buffer (%)", 0.0, 5.0, 2.0) / 100
rsi_limit = st.sidebar.slider("Soglia RSI(3)", 10, 70, 50)
min_price = st.sidebar.number_input("Prezzo Minimo Titolo ($)", value=1.0, step=0.5)
min_volume = st.sidebar.number_input("Volume Medio (20gg) Minimo", value=1000000, step=100000)
top_n = st.sidebar.number_input("Titoli in Portafoglio", value=10)

st.sidebar.divider()
st.sidebar.header("🕒 Backtest")
start_date = st.sidebar.date_input("Data Inizio Backtest", value=date(2023, 1, 1))

st.sidebar.divider()
email_dest = st.sidebar.text_input("Email per notifiche")
uploaded_file = st.sidebar.file_uploader("Carica CSV (Colonna 'Ticker')", type=["csv"])

# --- LOGICA PRINCIPALE ---
if uploaded_file:
    try:
        df_input = pd.read_csv(uploaded_file, sep=None, engine='python')
        df_input.columns = [c.strip().capitalize() for c in df_input.columns]
        
        if 'Ticker' not in df_input.columns:
            st.error("Errore: Il file deve contenere una colonna chiamata 'Ticker'.")
            st.stop()
            
        tickers = df_input['Ticker'].dropna().astype(str).tolist()
        if "SPY" not in [t.upper() for t in tickers]: tickers.append("SPY")
        
        with st.spinner('Download e analisi dati...'):
            # Scarichiamo dati sufficienti per coprire SMA200 dalla data di inizio
            full_data = yf.download(tickers, start=start_date - pd.Timedelta(days=365), interval="1d")
            close_data = full_data['Close']
            vol_data = full_data['Volume']
            
            if close_data.empty:
                st.error("Nessun dato scaricato. Controlla i ticker o la data.")
                st.stop()

            # Analisi Stato Mercato (Oggi)
            spy_p = close_data['SPY'].iloc[-1]
            spy_sma = close_data['SPY'].rolling(200).mean().iloc[-1]
            market_bull = spy_p > (spy_sma * (1 + spy_buffer))
            spy_status = "BULL" if market_bull else "BEAR"

            metrics = []
            for t in tickers:
                if t.upper() == "SPY" or t not in close_data.columns: continue
                
                s = close_data[t].dropna()
                v = vol_data[t].dropna()
                if len(s) < 200: continue
                
                p = s.iloc[-1]
                avg_vol = v.tail(20).mean() # Volume medio 20 giorni
                sma = s.rolling(200).mean().iloc[-1]
                roc = (p / s.iloc[-200]) - 1
                
                # RSI 3
                delta = s.diff()
                gain = delta.where(delta > 0, 0).rolling(3).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(3).mean()
                rsi = 100 - (100 / (1 + (gain / (loss + 0.001)).iloc[-1]))
                
                # FILTRI RICHIESTI
                if p >= min_price and avg_vol >= min_volume and p > sma and rsi < rsi_limit:
                    metrics.append({'Ticker': t, 'Price': p, 'Vol_Avg': avg_vol, 'ROC': roc, 'RSI3': rsi})
            
            df_res = pd.DataFrame(metrics).sort_values(by='ROC', ascending=False).head(top_n)

        st.title("📊 Weekly Rotation Dashboard")
        
        # Backtest su data selezionata
        portfolio_tickers = df_res['Ticker'].tolist()
        if portfolio_tickers:
            # Filtriamo i dati per la data di inizio selezionata
            bt_prices = close_data[portfolio_tickers].loc[start_date:]
            portfolio_returns = bt_prices.resample('W-MON').last().pct_change().mean(axis=1)
            equity_curve = (1 + portfolio_returns).cumprod().fillna(1)
            dd_series, mdd = calculate_drawdown(equity_curve)
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Regime Mercato", spy_status)
            c2.metric("Titoli Trovati (Filtri applicati)", len(metrics))
            c3.metric("Max Drawdown (Periodo)", f"{mdd:.2%}")

            st.subheader("🚀 Segnali Attuali (Basati su Prezzo e Volumi Minimi)")
            st.table(df_res.style.format({'Price': '{:.2f}', 'Vol_Avg': '{:,.0f}', 'ROC': '{:.2%}', 'RSI3': '{:.1f}'}))
            
            st.subheader(f"📈 Performance Portafoglio Attuale dal {start_date.strftime('%d/%m/%Y')}")
            st.line_chart(equity_curve)

            pdf_bytes = generate_pdf(df_res, spy_status, mdd, equity_curve.iloc[-1]-1)
            col_pdf, col_mail = st.columns(2)
            col_pdf.download_button("📥 Scarica Report PDF", pdf_bytes, f"Report_{datetime.now().strftime('%Y%m%d')}.pdf", "application/pdf")
            
            if col_mail.button("📧 Invia Report via Email"):
                if email_dest:
                    if send_email(email_dest, pdf_bytes, spy_status):
                        st.success("Email inviata correttamente!")
                else:
                    st.warning("Inserisci un indirizzo email.")
        else:
            st.warning("Nessun titolo soddisfa i criteri (Prezzo, Volume, SMA200, RSI) al momento.")
            
    except Exception as e:
        st.error(f"Errore: {e}")
else:
    st.warning("Carica un file CSV per iniziare l'analisi.")
