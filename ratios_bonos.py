import numpy as np
import requests
import simplejson
import pandas as pd
from datetime import datetime, timedelta
from tabulate import tabulate
from dash import dcc
import plotly.express as px
from dash import Dash, dash_table, html, Input, Output, callback
from dash.dash_table import FormatTemplate

token = "_ga=GA1.3.811343730.1712100922; _mtz_web_key=SFMyNTY.g3QAAAACbQAAAAtfY3NyZl90b2tlbm0AAAAYek1YQUEtam42dlhvNzZfRmRPTUtSWEVUbQAAAApzZXNzaW9uX2lkbQAAAEBjWnVFTmZuMXlHVFpabENTNlpsbXA2eHF5TkpybjJYdUZneU9HVU0zbkNJNHovZm5OcHhjcU9aakFsaDJJMndH.FiESbEGG8L5VG02EJva4kruF0rIDGpnexTSeJbCcjMo; SERVERID=pod-mtz-dma-web-758b4774f9-hj9q7"


def get_data(bono, token, _from, _to):
    r = requests.get(
        "https://matriz.veta.xoms.com.ar/api/v2/series/securities/bm_MERV_"
        + bono
        + "?resolution=1&from="
        + _from
        + "T14%3A02%3A12.000Z&to="
        + _to
        + "T23%3A50%3A52.000Z&_ds=1716836899361-984357",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": token,
        },
    )
    data = simplejson.loads(r.text)
    return data


def create_df():
    _from = datetime.now().strftime("%Y-%m-%d")
    _to = datetime.now().strftime("%Y-%m-%d")
    df_gd30 = pd.DataFrame.from_dict(get_data("GD30_24hs", token, _from, _to)["series"])
    df_gd35 = pd.DataFrame.from_dict(get_data("GD35_24hs", token, _from, _to)["series"])

    df = df_gd30.merge(df_gd35, left_on="d", right_on="d")
    df["Cociente"] = df["c_x"] / df["c_y"]

    df["d"] = pd.to_datetime(df["d"]) - pd.Timedelta("03:00:00")

    return df


def create_df_yest():
    _from = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _to = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    df_gd30 = pd.DataFrame.from_dict(get_data("GD30_24hs", token, _from, _to)["series"])
    df_gd35 = pd.DataFrame.from_dict(get_data("GD35_24hs", token, _from, _to)["series"])

    df = df_gd30.merge(df_gd35, left_on="d", right_on="d")
    df["Cociente"] = df["c_x"] / df["c_y"]

    df["d"] = pd.to_datetime(df["d"]) - pd.Timedelta("03:00:00")

    return df


app = Dash()

df = create_df()
df_yest = create_df_yest()

app.layout = [
    html.H2(children="GD30 / GD35", style={"textAlign": "center"}),
    dcc.Graph(
        id="graph-content",
        figure=px.line(df, x="d", y="Cociente", hover_data=["c_x", "c_y"])
        # .add_hline(
        #     y=df["Cociente"].mean(), line_color="rgb(254, 32, 32)", line_dash="dash"
        # )
        # .add_hline(y=df["Cociente"].max(), line_dash="dash")
        # .add_hline(y=df["Cociente"].min(), line_dash="dash")
        .add_hline(y=df_yest["Cociente"].mean(), line_color="#e57373", line_dash="dash")
        .add_hline(y=df_yest["Cociente"].max(), line_color="grey", line_dash="dash")
        .add_hline(y=df_yest["Cociente"].min(), line_color="grey", line_dash="dash"),
    ),
    html.Button(
        "Actualizar",
        id="actualizar",
        n_clicks=0,
        style={
            "align-items": "center",
            "display": "block",
            "margin-left": "auto",
            "margin-right": "auto",
            "padding": "6px 14px",
            "font-family": "-apple-system, BlinkMacSystemFont, 'Roboto', sans-serif",
            "border-radius": "6px",
            "border-color": "grey",
            "color": "#fff",
            "background": "linear-gradient(180deg, #4B91F7 0%, #367AF6 100%)",
            "background-origin": "border-box",
        },
    ),
]


@callback(Output("graph-content", "figure"), [Input("actualizar", "n_clicks")])
def update_output(n_clicks):
    global df
    df = create_df()
    return (
        px.line(df, x="d", y="Cociente", hover_data=["c_x", "c_y"])
        # .add_hline(
        #     y=df["Cociente"].mean(), line_color="rgb(254, 32, 32)", line_dash="dash"
        # )
        # .add_hline(y=df["Cociente"].max(), line_dash="dash")
        # .add_hline(y=df["Cociente"].min(), line_dash="dash")
        .add_hline(y=df_yest["Cociente"].mean(), line_color="#e57373", line_dash="dash")
        .add_hline(y=df_yest["Cociente"].max(), line_color="grey", line_dash="dash")
        .add_hline(y=df_yest["Cociente"].min(), line_color="grey", line_dash="dash")
    )


if __name__ == "__main__":
    app.run(debug=True)
