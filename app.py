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
def generate_pdf(top_picks, spy_status, mdd, total_ret, equity_fig_path, heatmap_fig_path):
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
    
    # Inserimento Immagini
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(200, 10, "Performance Storica e Heatmap:", ln=True)
    pdf.image(equity_fig_path, x=10, y=None, w=180)
    pdf.ln(2)
    pdf.image(heatmap_fig_path, x=10, y=None, w=180)
    
    # Titoli
    pdf.add_page()
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(200, 10, "Titoli Selezionati per la Settimana:", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.ln(5)
    for _, row in top_picks.iterrows():
        line = f"- {row['Ticker']}: Prezzo ${row['Price']:.2f} | ROC: {row['ROC']:.2%} | RSI: {row['RSI3']:.1f}"
        pdf.cell(200, 8, line, ln=True)
    
    return pdf.output(dest='S').encode('latin-1')

# --- INVIO EMAIL ---
def send_email(recipient_email, pdf_content, spy_status):
    try:
        sender_email = st.secrets["EMAIL_USER"]
        sender_password = st.secrets["EMAIL_PASSWORD"]
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = f"📊 Report Trading Settimanale - Regime {spy_status}"
        msg.attach(MIMEText("In allegato il report con grafici e heatmap.", 'plain'))
        part = MIMEApplication(pdf_content, Name=f"Report_{datetime.now().strftime('%Y%m%d')}.pdf")
        part['Content-Disposition'] = 'attachment; filename="Report_Settimanale.pdf"'
        msg.attach(part)
        
        server = smtplib.SMTP("smtp.gmail.com", 587)
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
min_price = st.sidebar.number_input("Prezzo Minimo ($)", value=1.0)
min_volume = st.sidebar.number_input("Volume Medio (20gg) Min", value=1000000)
top_n = st.sidebar.number_input("Titoli in Portafoglio", value=10)

st.sidebar.divider()
st.sidebar.header("🕒 Periodo Backtest")
start_date = st.sidebar.date_input("Inizio Backtest", value=date.today() - timedelta(days=365*3))
end_date = st.sidebar.date_input("Fine Backtest", value=date.today())

st.sidebar.divider()
email_dest = st.sidebar.text_input("Inserisci Email per notifiche")
uploaded_file = st.sidebar.file_uploader("Carica CSV (Colonna 'Ticker')", type=["csv"])

# --- LOGICA PRINCIPALE ---
if uploaded_file:
    try:
        df_input = pd.read_csv(uploaded_file, sep=None, engine='python')
        df_input.columns = [c.strip().capitalize() for c in df_input.columns]
        tickers = df_input['Ticker'].dropna().astype(str).tolist()
        if "SPY" not in [t.upper() for t in tickers]: tickers.append("SPY")
        
        with st.spinner('Download dati in corso...'):
            download_start = pd.to_datetime(start_date) - timedelta(days=365)
            full_data = yf.download(tickers, start=download_start, end=end_date, interval="1d")
            close_data = full_data['Close']
            vol_data = full_data['Volume']

        # Analisi Attuale
        metrics = []
        for t in tickers:
            if t.upper() == "SPY" or t not in close_data.columns: continue
            s = close_data[t].dropna()
            v = vol_data[t].dropna()
            if len(s) < 200: continue
            p, sma, roc = s.iloc[-1], s.rolling(200).mean().iloc[-1], (s.iloc[-1] / s.iloc[-200]) - 1
            rsi = 100 - (100 / (1 + (s.diff().where(s.diff() > 0, 0).rolling(3).mean() / ((-s.diff().where(s.diff() < 0, 0)).rolling(3).mean() + 0.001)).iloc[-1]))
            if p >= min_price and v.tail(20).mean() >= min_volume and p > sma and rsi < rsi_limit:
                metrics.append({'Ticker': t, 'Price': p, 'ROC': roc, 'RSI3': rsi})
        
        df_res = pd.DataFrame(metrics).sort_values(by='ROC', ascending=False).head(top_n)

        if not df_res.empty:
            st.title("📈 Analisi Completa Bensdorp")
            
            # Calcolo Backtest
            bt_prices = close_data[df_res['Ticker'].tolist()].loc[start_date:end_date]
            weekly_returns = bt_prices.resample('W-MON').last().pct_change().mean(axis=1)
            equity_curve = (1 + weekly_returns).cumprod().fillna(1)
            dd_series, mdd = calculate_drawdown(equity_curve)

            # --- GRAFICO EQUITY ---
            fig_eq, ax_eq = plt.subplots(figsize=(10, 4))
            ax_eq.plot(equity_curve, color='green')
            ax_eq.set_title("Equity Curve (Portafoglio Attuale nel Passato)")
            ax_eq.grid(True, alpha=0.3)
            st.pyplot(fig_eq)
            fig_eq.savefig("equity_tmp.png")

            # --- HEATMAP RENDIMENTI ---
            monthly_returns = weekly_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
            h_df = monthly_returns.to_frame(name='Return')
            h_df['Year'] = h_df.index.year
            h_df['Month'] = h_df.index.month_name()
            month_order = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
            pivot = h_df.pivot(index='Year', columns='Month', values='Return').reindex(columns=month_order)
            
            fig_hm, ax_hm = plt.subplots(figsize=(12, 5))
            sns.heatmap(pivot, annot=True, fmt=".1%", cmap="RdYlGn", center=0, ax=ax_hm)
            ax_hm.set_title("Heatmap Rendimenti Mensili")
            st.pyplot(fig_hm)
            fig_hm.savefig("heatmap_tmp.png")

            st.divider()
            st.subheader("🚀 Segnali per la Settimana")
            st.table(df_res.style.format({'Price': '{:.2f}', 'ROC': '{:.2%}', 'RSI3': '{:.1f}'}))

            # --- PDF E EMAIL ---
            pdf_bytes = generate_pdf(df_res, ("BULL" if close_data['SPY'].iloc[-1] > close_data['SPY'].rolling(200).mean().iloc[-1] else "BEAR"), mdd, equity_curve.iloc[-1]-1, "equity_tmp.png", "heatmap_tmp.png")
            
            c_pdf, c_mail = st.columns(2)
            c_pdf.download_button("📥 Scarica Report PDF Completo", pdf_bytes, f"Bensdorp_Report_{datetime.now().strftime('%Y%m%d')}.pdf", "application/pdf")
            
            if c_mail.button("📧 Invia Report via Email"):
                if email_dest:
                    if send_email(email_dest, pdf_bytes, "ANALISI"):
                        st.success("Report inviato con successo!")
                else:
                    st.warning("Inserisci l'email nella sidebar.")

            # Pulizia file temporanei
            if os.path.exists("equity_tmp.png"): os.remove("equity_tmp.png")
            if os.path.exists("heatmap_tmp.png"): os.remove("heatmap_tmp.png")

        else:
            st.warning("Nessun titolo soddisfa i criteri con questi filtri.")

    except Exception as e:
        st.error(f"Errore: {e}")
