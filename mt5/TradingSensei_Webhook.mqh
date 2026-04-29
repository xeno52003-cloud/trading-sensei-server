//+------------------------------------------------------------------+
//|                                       TradingSensei_Webhook.mqh  |
//|                              Drop-in webhook bridge for the      |
//|                              Trading Sensei server.              |
//|                                                                  |
//|  Usage: place this file under MQL5\Include\ and add to your EA:  |
//|    #include <TradingSensei_Webhook.mqh>                          |
//|                                                                  |
//|  See mt5/README.md for the full integration walkthrough.         |
//+------------------------------------------------------------------+
#property strict

#include <Trade/Trade.mqh>

input group "==== TRADING SENSEI WEBHOOK ===="
input bool   InpWebhookEnabled       = true;                       // Enable webhook bridge
input string InpWebhookURL           = "https://your-server.com";  // Server base URL (no trailing slash)
input string InpWebhookSecret        = "";                         // X-EA-Secret value (must match server EA_SECRET)
input int    InpWebhookHeartbeatSec  = 5;                          // Heartbeat interval (seconds)
input int    InpWebhookMagicNumber   = 123456;                     // Used to scope close_all commands

// Public flag the EA's signal logic should respect. Server start/stop/pause
// commands flip this, so your OnTick() can `if (!g_webhook_ea_running) return;`
// before opening new trades.
bool g_webhook_ea_running = true;

// --- internals ---
CTrade   _ts_trade;
datetime _ts_last_heartbeat = 0;
datetime _ts_last_account   = 0;
int      _ts_uptime_start   = 0;

//+------------------------------------------------------------------+
//| Call this once from OnInit().                                    |
//+------------------------------------------------------------------+
void WebhookInit()
{
    _ts_uptime_start = (int)TimeCurrent();
    _ts_trade.SetExpertMagicNumber(InpWebhookMagicNumber);
}

//+------------------------------------------------------------------+
//| Send a JSON POST. Returns the response body, or "" on failure.   |
//+------------------------------------------------------------------+
string _ts_post(string path, string payload)
{
    if(!InpWebhookEnabled || InpWebhookURL == "") return "";

    string headers = "Content-Type: application/json\r\n"
                     "X-EA-Secret: " + InpWebhookSecret + "\r\n";
    char   post[], result[];
    string response_headers;
    StringToCharArray(payload, post, 0, StringLen(payload));

    int code = WebRequest("POST", InpWebhookURL + path, headers, 5000,
                          post, result, response_headers);
    if(code == -1)
    {
        Print("Webhook error ", GetLastError(),
              " — add ", InpWebhookURL,
              " to Tools → Options → Expert Advisors → Allow WebRequest list");
        return "";
    }
    if(code < 200 || code >= 300)
        Print("Webhook ", path, " returned HTTP ", code, ": ", CharArrayToString(result));

    return CharArrayToString(result);
}

//+------------------------------------------------------------------+
//| Tiny JSON value extractors — sufficient for our flat payloads.   |
//+------------------------------------------------------------------+
string _ts_json_str(string json, string key)
{
    string needle = "\"" + key + "\":\"";
    int start = StringFind(json, needle);
    if(start < 0) return "";
    start += StringLen(needle);
    int end = StringFind(json, "\"", start);
    if(end < 0) return "";
    return StringSubstr(json, start, end - start);
}

double _ts_json_double(string json, string key)
{
    string needle = "\"" + key + "\":";
    int start = StringFind(json, needle);
    if(start < 0) return 0.0;
    start += StringLen(needle);
    int end = start;
    int len = StringLen(json);
    while(end < len)
    {
        ushort c = StringGetCharacter(json, end);
        if(c == ',' || c == '}' || c == ']') break;
        end++;
    }
    return StringToDouble(StringSubstr(json, start, end - start));
}

//+------------------------------------------------------------------+
//| Heartbeat — call every tick. Internal throttle limits frequency. |
//| Drains any commands the server queued since the last call.       |
//+------------------------------------------------------------------+
void WebhookHeartbeat()
{
    if(TimeCurrent() - _ts_last_heartbeat < InpWebhookHeartbeatSec) return;
    _ts_last_heartbeat = TimeCurrent();

    string payload = StringFormat(
        "{\"running\":%s,\"symbol\":\"%s\",\"timeframe\":\"%s\",\"uptime\":%d,\"version\":\"2.0.0\"}",
        g_webhook_ea_running ? "true" : "false",
        _Symbol,
        EnumToString(Period()),
        (int)TimeCurrent() - _ts_uptime_start
    );

    string response = _ts_post("/webhook/ea/heartbeat", payload);
    if(response != "") _ts_dispatch_commands(response);
}

//+------------------------------------------------------------------+
//| Walk "commands":[ {...}, {...} ] and dispatch each.              |
//+------------------------------------------------------------------+
void _ts_dispatch_commands(string json)
{
    int idx = 0;
    while(true)
    {
        int next = StringFind(json, "{\"action\":", idx);
        if(next < 0) break;
        int end = StringFind(json, "}", next);
        if(end < 0) break;
        _ts_dispatch_one(StringSubstr(json, next, end - next + 1));
        idx = end + 1;
    }
}

void _ts_dispatch_one(string cmd)
{
    string action = _ts_json_str(cmd, "action");
    Print("📡 Webhook command: ", action);

    if(action == "start")
        g_webhook_ea_running = true;
    else if(action == "stop" || action == "pause")
        g_webhook_ea_running = false;
    else if(action == "close_trade")
    {
        ulong ticket = (ulong)StringToInteger(_ts_json_str(cmd, "trade_id"));
        if(ticket > 0 && PositionSelectByTicket(ticket))
            _ts_trade.PositionClose(ticket);
    }
    else if(action == "close_all")
    {
        for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
            ulong t = PositionGetTicket(i);
            if(t > 0 && PositionGetInteger(POSITION_MAGIC) == InpWebhookMagicNumber)
                _ts_trade.PositionClose(t);
        }
    }
    else if(action == "modify_trade")
    {
        ulong ticket = (ulong)StringToInteger(_ts_json_str(cmd, "trade_id"));
        if(ticket > 0 && PositionSelectByTicket(ticket))
        {
            double sl = _ts_json_double(cmd, "sl");
            double tp = _ts_json_double(cmd, "tp");
            if(sl == 0) sl = PositionGetDouble(POSITION_SL);
            if(tp == 0) tp = PositionGetDouble(POSITION_TP);
            _ts_trade.PositionModify(ticket, sl, tp);
        }
    }
}

//+------------------------------------------------------------------+
//| Trade lifecycle reporters — call from your EA when trades happen.|
//+------------------------------------------------------------------+
void WebhookTradeOpened(ulong ticket, string trade_type, double lots, double entry,
                        double sl, double tp, int signal_strength)
{
    string payload = StringFormat(
        "{\"ticket\":\"%I64u\",\"symbol\":\"%s\",\"type\":\"%s\",\"lots\":%.2f,"
        "\"entry\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"signal\":%d,\"open_time\":\"%s\"}",
        ticket, _Symbol, trade_type, lots, entry, sl, tp, signal_strength,
        TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS)
    );
    _ts_post("/webhook/ea/trade/open", payload);
}

void WebhookTradeClosed(ulong ticket, double pnl, double close_price)
{
    string payload = StringFormat(
        "{\"ticket\":\"%I64u\",\"pnl\":%.2f,\"close_price\":%.5f}",
        ticket, pnl, close_price
    );
    _ts_post("/webhook/ea/trade/close", payload);
}

void WebhookTradeUpdate(ulong ticket, double current_price, double pnl, double pips)
{
    string payload = StringFormat(
        "{\"ticket\":\"%I64u\",\"current\":%.5f,\"pnl\":%.2f,\"pips\":%.1f}",
        ticket, current_price, pnl, pips
    );
    _ts_post("/webhook/ea/trade/update", payload);
}

void WebhookSignal(string trade_type, int strength)
{
    string payload = StringFormat(
        "{\"symbol\":\"%s\",\"type\":\"%s\",\"strength\":%d}",
        _Symbol, trade_type, strength
    );
    _ts_post("/webhook/ea/signal", payload);
}

//+------------------------------------------------------------------+
//| Account snapshot — call periodically (e.g. every 30s).           |
//+------------------------------------------------------------------+
void WebhookAccountUpdate()
{
    if(TimeCurrent() - _ts_last_account < 30) return;
    _ts_last_account = TimeCurrent();

    int open_count = 0;
    for(int i = 0; i < PositionsTotal(); i++)
        if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == InpWebhookMagicNumber)
            open_count++;

    string payload = StringFormat(
        "{\"balance\":%.2f,\"equity\":%.2f,\"margin_used\":%.2f,\"margin_available\":%.2f,"
        "\"unrealized_pnl\":%.2f,\"open_trades\":%d}",
        AccountInfoDouble(ACCOUNT_BALANCE),
        AccountInfoDouble(ACCOUNT_EQUITY),
        AccountInfoDouble(ACCOUNT_MARGIN),
        AccountInfoDouble(ACCOUNT_MARGIN_FREE),
        AccountInfoDouble(ACCOUNT_PROFIT),
        open_count
    );
    _ts_post("/webhook/ea/account", payload);
}
