import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from database import (
    execute_query,
    get_performance_stats,
    get_league_performance,
    health_check
)

# Page config
st.set_page_config(
    page_title="⚽ Football Predictions Dashboard",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
    }
    .stMetric {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<h1 class="main-header">⚽ FOOTBALL PREDICTIONS DASHBOARD</h1>', unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/color/96/000000/football.png", width=80)
    st.title("⚙️ Ayarlar")
    
    # Date range selector
    date_range = st.selectbox(
        "📅 Zaman Aralığı",
        ["Son 7 Gün", "Son 30 Gün", "Son 90 Gün", "Tüm Zamanlar"],
        index=1
    )
    
    days_map = {
        "Son 7 Gün": 7,
        "Son 30 Gün": 30,
        "Son 90 Gün": 90,
        "Tüm Zamanlar": 36500
    }
    selected_days = days_map[date_range]
    
    # Risk filter
    risk_filter = st.multiselect(
        "🎯 Risk Seviyesi",
        ["LOW", "MEDIUM", "HIGH"],
        default=["LOW", "MEDIUM", "HIGH"]
    )
    
    # League filter
    league_filter = st.text_input("🏆 Lig Filtrele (opsiyonel)")
    
    st.divider()
    
    # Database health check
    is_healthy, latency = health_check()
    if is_healthy:
        st.success(f"✅ Database Bağlantısı: {latency}ms")
    else:
        st.error("❌ Database Bağlantısı: Hatalı")
    
    st.divider()
    st.caption("🤖 Powered by DeepSeek AI")
    st.caption("💻 Developed with Streamlit")

# Main content
tab1, tab2, tab3, tab4 = st.tabs(["📊 Genel Bakış", "📈 Performans", "🏆 Ligler", "📋 Tahminler"])

# ============================================
# TAB 1: GENEL BAKIŞ
# ============================================
with tab1:
    # Get performance stats
    stats = get_performance_stats(selected_days)
    
    if stats and stats['total_predictions'] > 0:
        # Key Metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric(
                "🎯 Toplam Tahmin",
                f"{stats['total_predictions']}",
                delta=None
            )
        
        with col2:
            accuracy = stats['accuracy_rate'] or 0
            st.metric(
                "✅ Başarı Oranı",
                f"%{accuracy:.1f}",
                delta=f"%{accuracy - 50:.1f}" if accuracy > 50 else f"%{accuracy - 50:.1f}"
            )
        
        with col3:
            correct = stats['correct_predictions'] or 0
            st.metric(
                "🟢 Doğru",
                f"{correct}",
                delta=None
            )
        
        with col4:
            wrong = stats['total_predictions'] - correct
            st.metric(
                "🔴 Yanlış",
                f"{wrong}",
                delta=None
            )
        
        with col5:
            profit = stats['total_profit_loss'] or 0
            st.metric(
                "💰 Kar/Zarar",
                f"{profit:+.0f} TL",
                delta=f"{profit:+.0f} TL"
            )
        
        st.divider()
        
        # Charts
        col_left, col_right = st.columns(2)
        
        with col_left:
            st.subheader("📊 Başarı Dağılımı")
            
            # Pie chart
            fig_pie = go.Figure(data=[go.Pie(
                labels=['Doğru', 'Yanlış'],
                values=[correct, wrong],
                hole=0.4,
                marker_colors=['#00c853', '#ff1744']
            )])
            fig_pie.update_layout(
                height=300,
                showlegend=True,
                annotations=[dict(text=f'%{accuracy:.1f}', x=0.5, y=0.5, font_size=20, showarrow=False)]
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        
        with col_right:
            st.subheader("🎲 Risk Dağılımı")
            
            # Risk distribution
            risk_query = f"""
                SELECT 
                    risk_level,
                    COUNT(*) as count
                FROM predictions
                WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
                    AND result IS NOT NULL
                    AND risk_level IS NOT NULL
                GROUP BY risk_level
                ORDER BY risk_level;
            """
            risk_data = execute_query(risk_query)
            
            if risk_data:
                df_risk = pd.DataFrame(risk_data)
                fig_risk = px.bar(
                    df_risk,
                    x='risk_level',
                    y='count',
                    color='risk_level',
                    color_discrete_map={'LOW': '#00c853', 'MEDIUM': '#ffc107', 'HIGH': '#ff1744'},
                    labels={'risk_level': 'Risk Seviyesi', 'count': 'Tahmin Sayısı'}
                )
                fig_risk.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig_risk, use_container_width=True)
        
        st.divider()
        
        # Daily performance trend
        st.subheader("📈 Günlük Performans Trendi")
        
        daily_query = f"""
            SELECT 
                DATE(match_date) as day,
                COUNT(*) as total,
                COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
                SUM(COALESCE(profit_loss, 0)) as profit
            FROM predictions
            WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
                AND result IS NOT NULL
            GROUP BY DATE(match_date)
            ORDER BY day;
        """
        daily_data = execute_query(daily_query)
        
        if daily_data:
            df_daily = pd.DataFrame(daily_data)
            df_daily['accuracy'] = (df_daily['correct'] / df_daily['total'] * 100).round(1)
            df_daily['cumulative_profit'] = df_daily['profit'].cumsum()
            
            fig_trend = go.Figure()
            
            # Accuracy line
            fig_trend.add_trace(go.Scatter(
                x=df_daily['day'],
                y=df_daily['accuracy'],
                name='Başarı Oranı (%)',
                line=dict(color='#667eea', width=3),
                yaxis='y'
            ))
            
            # Cumulative profit line
            fig_trend.add_trace(go.Scatter(
                x=df_daily['day'],
                y=df_daily['cumulative_profit'],
                name='Kümülatif Kâr (TL)',
                line=dict(color='#764ba2', width=3),
                yaxis='y2'
            ))
            
            fig_trend.update_layout(
                height=400,
                xaxis=dict(title='Tarih'),
                yaxis=dict(title='Başarı Oranı (%)', side='left', showgrid=False),
                yaxis2=dict(title='Kümülatif Kâr (TL)', side='right', overlaying='y', showgrid=False),
                hovermode='x unified'
            )
            
            st.plotly_chart(fig_trend, use_container_width=True)
    
    else:
        st.warning("⚠️ Bu tarih aralığında veri bulunamadı.")

# ============================================
# TAB 2: PERFORMANS ANALİZİ
# ============================================
with tab2:
    st.subheader("📊 Detaylı Performans Analizi")
    
    # Confidence vs Accuracy
    conf_query = f"""
        SELECT 
            CASE 
                WHEN ai_confidence >= 80 THEN '80-100%'
                WHEN ai_confidence >= 70 THEN '70-80%'
                WHEN ai_confidence >= 60 THEN '60-70%'
                ELSE '< 60%'
            END as confidence_range,
            COUNT(*) as total,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
            ROUND(COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / COUNT(*) * 100, 1) as accuracy
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
            AND result IS NOT NULL
            AND ai_confidence IS NOT NULL
        GROUP BY confidence_range
        ORDER BY confidence_range DESC;
    """
    conf_data = execute_query(conf_query)
    
    if conf_data:
        df_conf = pd.DataFrame(conf_data)
        
        col1, col2 = st.columns(2)
        
        with col1:
            fig_conf = px.bar(
                df_conf,
                x='confidence_range',
                y='accuracy',
                color='accuracy',
                color_continuous_scale='RdYlGn',
                labels={'confidence_range': 'Güven Aralığı', 'accuracy': 'Başarı Oranı (%)'},
                title='Güven Skoru vs Başarı Oranı'
            )
            fig_conf.update_layout(height=400, showlegend=False)
            st.plotly_chart(fig_conf, use_container_width=True)
        
        with col2:
            fig_total = px.bar(
                df_conf,
                x='confidence_range',
                y='total',
                color='total',
                color_continuous_scale='Blues',
                labels={'confidence_range': 'Güven Aralığı', 'total': 'Tahmin Sayısı'},
                title='Güven Aralığına Göre Tahmin Dağılımı'
            )
            fig_total.update_layout(height=400, showlegend=False)
            st.plotly_chart(fig_total, use_container_width=True)
    
    st.divider()
    
    # Prediction type performance
    st.subheader("🎯 Tahmin Tipi Bazlı Performans")
    
    pred_query = f"""
        SELECT 
            ai_prediction as prediction_type,
            COUNT(*) as total,
            COUNT(CASE WHEN is_correct = TRUE THEN 1 END) as correct,
            ROUND(COUNT(CASE WHEN is_correct = TRUE THEN 1 END)::DECIMAL / COUNT(*) * 100, 1) as accuracy,
            SUM(COALESCE(profit_loss, 0)) as profit
        FROM predictions
        WHERE match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'
            AND result IS NOT NULL
            AND ai_prediction IS NOT NULL
            AND ai_prediction NOT LIKE '%Form:%'
        GROUP BY ai_prediction
        ORDER BY total DESC
        LIMIT 10;
    """
    pred_data = execute_query(pred_query)
    
    if pred_data:
        df_pred = pd.DataFrame(pred_data)
        
        fig_pred = go.Figure()
        
        fig_pred.add_trace(go.Bar(
            name='Toplam',
            x=df_pred['prediction_type'],
            y=df_pred['total'],
            marker_color='lightblue'
        ))
        
        fig_pred.add_trace(go.Bar(
            name='Doğru',
            x=df_pred['prediction_type'],
            y=df_pred['correct'],
            marker_color='green'
        ))
        
        fig_pred.update_layout(
            height=400,
            barmode='group',
            xaxis_title='Tahmin Tipi',
            yaxis_title='Sayı'
        )
        
        st.plotly_chart(fig_pred, use_container_width=True)
        
        # Table
        st.dataframe(
            df_pred.style.background_gradient(subset=['accuracy'], cmap='RdYlGn'),
            use_container_width=True
        )

# ============================================
# TAB 3: LİG PERFORMANSI
# ============================================
with tab3:
    st.subheader("🏆 Lig Bazlı Performans")
    
    league_data = get_league_performance(selected_days)
    
    if league_data:
        df_league = pd.DataFrame(league_data)
        
        # Top performing leagues
        st.markdown("### 🥇 En Başarılı Ligler")
        
        fig_league = px.bar(
            df_league.head(10),
            x='league',
            y='accuracy_rate',
            color='accuracy_rate',
            color_continuous_scale='RdYlGn',
            labels={'league': 'Lig', 'accuracy_rate': 'Başarı Oranı (%)'},
            hover_data=['total_predictions', 'total_profit_loss']
        )
        fig_league.update_layout(height=400, showlegend=False)
        fig_league.update_xaxes(tickangle=45)
        st.plotly_chart(fig_league, use_container_width=True)
        
        st.divider()
        
        # League performance table
        st.markdown("### 📊 Tüm Ligler Detay")
        
        # Add emoji based on profit
        def profit_emoji(val):
            if val > 0:
                return f"🟢 +{val:.0f} TL"
            elif val < 0:
                return f"🔴 {val:.0f} TL"
            else:
                return "⚪ 0 TL"
        
        df_league['Kar/Zarar'] = df_league['total_profit_loss'].apply(profit_emoji)
        df_league['Başarı'] = df_league['accuracy_rate'].apply(lambda x: f"%{x:.1f}")
        
        display_df = df_league[['league', 'total_predictions', 'correct_predictions', 'Başarı', 'Kar/Zarar']]
        display_df.columns = ['Lig', 'Toplam', 'Doğru', 'Başarı Oranı', 'Kar/Zarar']
        
        st.dataframe(
            display_df,
            use_container_width=True,
            height=400
        )
    else:
        st.warning("⚠️ Lig performans verisi bulunamadı.")

# ============================================
# TAB 4: TAHMİN LİSTESİ
# ============================================
with tab4:
    st.subheader("📋 Tüm Tahminler")
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        result_filter = st.selectbox(
            "Sonuç Durumu",
            ["Tümü", "Doğru", "Yanlış", "Beklemede"]
        )
    
    with col2:
        sort_by = st.selectbox(
            "Sırala",
            ["Tarih (Yeni)", "Tarih (Eski)", "Güven (Yüksek)", "Kâr (Yüksek)"]
        )
    
    with col3:
        limit = st.number_input("Göster", min_value=10, max_value=100, value=20, step=10)
    
    # Build query
    risk_filter_sql = "'" + "','".join(risk_filter) + "'"
    
    where_clauses = [
        f"match_date >= CURRENT_DATE - INTERVAL '{selected_days} days'",
        f"risk_level IN ({risk_filter_sql})"
    ]
    
    if league_filter:
        where_clauses.append(f"league ILIKE '%{league_filter}%'")
    
    if result_filter == "Doğru":
        where_clauses.append("is_correct = TRUE")
    elif result_filter == "Yanlış":
        where_clauses.append("is_correct = FALSE")
    elif result_filter == "Beklemede":
        where_clauses.append("result IS NULL AND match_date > NOW()")
    
    order_map = {
        "Tarih (Yeni)": "match_date DESC",
        "Tarih (Eski)": "match_date ASC",
        "Güven (Yüksek)": "ai_confidence DESC",
        "Kâr (Yüksek)": "profit_loss DESC NULLS LAST"
    }
    
    predictions_query = f"""
        SELECT 
            match_id,
            home_team,
            away_team,
            league,
            match_date,
            ai_prediction,
            ai_confidence,
            risk_level,
            recommended_bet,
            result,
            is_correct,
            profit_loss,
            telegram_sent
        FROM predictions
        WHERE {' AND '.join(where_clauses)}
        ORDER BY {order_map[sort_by]}
        LIMIT {limit};
    """
    
    predictions = execute_query(predictions_query)
    
    if predictions:
        # Display as cards
        for pred in predictions:
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 1])
                
                with col1:
                    # Match info
                    match_emoji = "⚽"
                    if pred['is_correct'] == True:
                        match_emoji = "✅"
                    elif pred['is_correct'] == False:
                        match_emoji = "❌"
                    
                    st.markdown(f"### {match_emoji} {pred['home_team']} vs {pred['away_team']}")
                    st.caption(f"🏆 {pred['league']} | 📅 {pred['match_date'].strftime('%d.%m.%Y %H:%M')}")
                
                with col2:
                    # Prediction
                    risk_emoji = {'LOW': '🟢', 'MEDIUM': '🟡', 'HIGH': '🔴'}.get(pred['risk_level'], '⚪')
                    st.markdown(f"**Tahmin:** {pred['ai_prediction']}")
                    st.markdown(f"**Güven:** {pred['ai_confidence']:.0f}% | **Risk:** {risk_emoji} {pred['risk_level']}")
                
                with col3:
                    # Result
                    if pred['result']:
                        st.markdown(f"**Sonuç:** {pred['result']}")
                        if pred['profit_loss']:
                            profit_color = "green" if pred['profit_loss'] > 0 else "red"
                            st.markdown(f"**Kâr:** <span style='color:{profit_color}'>{pred['profit_loss']:+.0f} TL</span>", unsafe_allow_html=True)
                    else:
                        st.markdown("**Durum:** Beklemede...")
                
                st.divider()
    else:
        st.info("ℹ️ Seçilen filtrelere uygun tahmin bulunamadı.")

# Footer
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.caption("🤖 Powered by DeepSeek AI")
with col2:
    st.caption("⚽ Football Prediction System v2.1")
with col3:
    st.caption(f"📅 Son güncelleme: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
