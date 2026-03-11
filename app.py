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

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Bensdorp Weekly Pro", layout="wide")

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
    pdf.cell(200, 10, "Titoli Selezionati:", ln=True)
    pdf.set_font("Arial", '', 10)
    for _, row in top_picks.iterrows():
        line = f"- {row['Ticker']}: Prezzo ${row['Price']:.2f} | ROC: {row['ROC']:.2%} | RSI: {row['RSI3']:.1f}"
        pdf.cell(200, 8, line, ln=True)
    return pdf.output(dest='S').encode('latin-1')

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
email_dest = st.sidebar.text_input("Email per notifiche")
uploaded_file = st.sidebar.file_uploader("Carica CSV (Colonna 'Ticker')", type=["csv"])

# --- LOGICA PRINCIPALE ---
if uploaded_file:
    try:
        df_input = pd.read_csv(uploaded_file, sep=None, engine='python')
        df_input.columns = [c.strip().capitalize() for c in df_input.columns]
        tickers = df_input['Ticker'].dropna().astype(str).tolist()
        if "SPY" not in [t.upper() for t in tickers]: tickers.append("SPY")
        
        with st.spinner('Download dati...'):
            # Scarichiamo dati estesi per permettere SMA200 anche all'inizio del backtest
            download_start = pd.to_datetime(start_date) - timedelta(days=365)
            full_data = yf.download(tickers, start=download_start, end=end_date, interval="1d")
            close_data = full_data['Close']
            vol_data = full_data['Volume']

        # 1. Calcolo Metriche Attuali (Oggi)
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

        # 2. Backtest e Heatmap
        st.title("📈 Bensdorp Advanced Analytics")
        
        if not df_res.empty:
            # Equità portafoglio attuale applicata al passato
            bt_prices = close_data[df_res['Ticker'].tolist()].loc[start_date:end_date]
            weekly_returns = bt_prices.resample('W-MON').last().pct_change().mean(axis=1)
            equity_curve = (1 + weekly_returns).cumprod().fillna(1)
            dd_series, mdd = calculate_drawdown(equity_curve)

            # --- GENERAZIONE HEATMAP ---
            st.subheader("🗓️ Heatmap Rendimenti Mensili (Ultimi 3 Anni)")
            monthly_returns = weekly_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
            
            heatmap_df = monthly_returns.to_frame(name='Return')
            heatmap_df['Year'] = heatmap_df.index.year
            heatmap_df['Month'] = heatmap_df.index.month_name()
            
            # Ordiniamo i mesi correttamente
            month_order = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
            pivot_table = heatmap_df.pivot(index='Year', columns='Month', values='Return')
            pivot_table = pivot_table.reindex(columns=month_order)

            fig, ax = plt.subplots(figsize=(12, 5))
            sns.heatmap(pivot_table, annot=True, fmt=".1%", cmap="RdYlGn", center=0, cbar_kws={'label': 'Rendimento'}, ax=ax)
            plt.title("Rendimenti Mensili del Portafoglio Selezionato")
            st.pyplot(fig)
            

            # --- METRICHE E GRAFICI ---
            c1, c2, c3 = st.columns(3)
            c1.metric("Rendimento Totale", f"{(equity_curve.iloc[-1]-1):.2%}")
            c2.metric("Max Drawdown", f"{mdd:.2%}")
            c3.metric("MAR Ratio", f"{(abs((equity_curve.iloc[-1]-1)/mdd) if mdd !=0 else 0):.2f}")

            st.line_chart(equity_curve)
            st.subheader("🚀 Titoli da Ruotare (Selezione Corrente)")
            st.table(df_res.style.format({'Price': '{:.2f}', 'ROC': '{:.2%}', 'RSI3': '{:.1f}'}))
            
        else:
            st.warning("Nessun titolo soddisfa i criteri per il periodo selezionato.")

    except Exception as e:
        st.error(f"Errore: {e}")
else:
    st.info("Carica il file CSV per iniziare l'analisi quantitativa.")
