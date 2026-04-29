//+------------------------------------------------------------------+
//|                                       TradingSensei_Example.mq5  |
//|                                                                  |
//|  Minimal EA showing how to wire TradingSensei_Webhook.mqh into a |
//|  real strategy. Drop your existing signal logic into the marked  |
//|  spots — every other line is the bridge.                         |
//+------------------------------------------------------------------+
#property copyright "Master Xeno Trading Systems"
#property version   "1.00"
#property strict

#include <TradingSensei_Webhook.mqh>

datetime _last_bar_time = 0;

int OnInit()
{
    WebhookInit();                // ① register magic number + uptime
    Print("✓ Trading Sensei example EA initialised");
    return(INIT_SUCCEEDED);
}

void OnTick()
{
    WebhookHeartbeat();           // ② drains any commands the app queued
    WebhookAccountUpdate();       // ③ refreshes balance/equity on the dashboard

    // Only do strategy work on a new bar
    datetime now = iTime(_Symbol, PERIOD_CURRENT, 0);
    if(now == _last_bar_time) return;
    _last_bar_time = now;

    // Respect remote start/stop/pause
    if(!g_webhook_ea_running) return;

    // ─── YOUR SIGNAL LOGIC HERE ──────────────────────────────────
    // int strength = 0;
    // int direction = AnalyzeMarket(strength);
    // if(direction == 1)  OpenBuy(strength);
    // if(direction == -1) OpenSell(strength);
    // ─────────────────────────────────────────────────────────────
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
    // ④ report opens and closes as they happen
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

    if(!HistoryDealSelect(trans.deal)) return;
    if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpWebhookMagicNumber) return;

    ulong  ticket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
    double price  = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
    ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);

    if(entry == DEAL_ENTRY_IN)
    {
        long deal_type = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
        string side    = (deal_type == DEAL_TYPE_BUY) ? "BUY" : "SELL";
        double lots    = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
        if(PositionSelectByTicket(ticket))
            WebhookTradeOpened(ticket, side, lots, price,
                               PositionGetDouble(POSITION_SL),
                               PositionGetDouble(POSITION_TP),
                               5);
    }
    else if(entry == DEAL_ENTRY_OUT)
    {
        double pnl = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
        WebhookTradeClosed(ticket, pnl, price);
    }
}
