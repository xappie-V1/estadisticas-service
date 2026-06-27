"""
estadisticas-service
====================
Microservicio de ESTADÍSTICAS del casino (FastAPI, solo lectura).

Agrega KPIs sobre las tablas compartidas (transacciones, usuarios, apuestas) y
los expone para el dashboard del frontend. Comparte BD y JWT con casino-backend.

Prefijo de rutas: /api/estadisticas
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import usuario_actual
from .db import conexion, dict_cursor, esperar_bd, ping


@asynccontextmanager
async def lifespan(app: FastAPI):
    esperar_bd()
    yield


app = FastAPI(
    title="Estadísticas Service",
    description="Dashboards y KPIs del casino (Módulo 3 - ISY1101)",
    version="1.0.0",
    lifespan=lifespan,
)

_origenes = [o.strip() for o in os.getenv("CORS_ORIGIN", "http://localhost:4200").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origenes,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# TODO (alumno): implementar las rutas de salud que usará Kubernetes:
#   - liveness: ¿el proceso está vivo? (respuesta simple).
#   - readiness: ¿está listo para recibir tráfico? Debe verificar la BD.
# Luego configurar livenessProbe/readinessProbe en el Deployment de EKS.

@app.get("/livez")
def liveness():
    return {
        "status": "ok",
        "service": "estadisticas-service"
    }


@app.get("/readyz")
def readiness():
    if ping():
        return {
            "status": "ready",
            "database": "connected"
        }

    return JSONResponse(
        status_code=503,
        content={
            "status": "not ready",
            "database": "disconnected"
        }
    )


@app.get("/api/estadisticas/mias")
def mis_estadisticas(usuario: dict = Depends(usuario_actual)):
    """KPIs, desglose por tipo y evolución de saldo del usuario autenticado."""
    uid = usuario["id"]
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """SELECT
                     COALESCE(SUM(monto) FILTER (WHERE tipo = 'apuesta'), 0)  AS total_apostado,
                     COALESCE(SUM(monto) FILTER (WHERE tipo = 'premio'), 0)   AS total_premios,
                     COALESCE(SUM(monto) FILTER (WHERE tipo = 'deposito'), 0) AS total_depositos,
                     COUNT(*) FILTER (WHERE tipo = 'apuesta')                 AS n_apuestas
                   FROM transacciones WHERE usuario_id = %s""",
                (uid,),
            )
            r = cur.fetchone()
            cur.execute("SELECT saldo FROM usuarios WHERE id = %s", (uid,))
            saldo = cur.fetchone()
            saldo_actual = saldo["saldo"] if saldo else 0

            cur.execute(
                """SELECT tipo, COALESCE(SUM(monto),0) AS total, COUNT(*) AS count
                     FROM transacciones WHERE usuario_id = %s
                    GROUP BY tipo ORDER BY total DESC""",
                (uid,),
            )
            por_tipo = cur.fetchall()

            cur.execute(
                """SELECT creada_en AS fecha, saldo_post
                     FROM transacciones WHERE usuario_id = %s
                    ORDER BY creada_en DESC LIMIT 30""",
                (uid,),
            )
            linea = list(reversed(cur.fetchall()))

    neto = round((r["total_premios"] or 0) - (r["total_apostado"] or 0), 2)
    return {
        "resumen": {
            "total_apostado": r["total_apostado"],
            "total_premios": r["total_premios"],
            "total_depositos": r["total_depositos"],
            "neto": neto,
            "n_apuestas": r["n_apuestas"],
            "saldo_actual": saldo_actual,
        },
        "por_tipo": por_tipo,
        "linea_saldo": linea,
    }


@app.get("/api/estadisticas/globales")
def estadisticas_globales(usuario: dict = Depends(usuario_actual)):
    """KPIs de toda la plataforma: usuarios, GGR, top jugadores y apuestas."""
    with conexion() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(saldo),0) AS saldo FROM usuarios")
            u = cur.fetchone()

            cur.execute(
                """SELECT
                     COALESCE(SUM(monto) FILTER (WHERE tipo = 'apuesta'), 0) AS apostado,
                     COALESCE(SUM(monto) FILTER (WHERE tipo = 'premio'), 0)  AS premios
                   FROM transacciones"""
            )
            g = cur.fetchone()

            cur.execute(
                """SELECT tipo, COALESCE(SUM(monto),0) AS total, COUNT(*) AS count
                     FROM transacciones GROUP BY tipo ORDER BY total DESC"""
            )
            por_tipo = cur.fetchall()

            cur.execute(
                "SELECT username, saldo FROM usuarios ORDER BY saldo DESC LIMIT 5"
            )
            top = cur.fetchall()

            cur.execute(
                """SELECT
                     COUNT(*) AS total,
                     COUNT(*) FILTER (WHERE estado = 'ganada')    AS ganadas,
                     COUNT(*) FILTER (WHERE estado = 'perdida')   AS perdidas,
                     COUNT(*) FILTER (WHERE estado = 'pendiente') AS pendientes
                   FROM apuestas"""
            )
            ap = cur.fetchone()

    resueltas = (ap["ganadas"] or 0) + (ap["perdidas"] or 0)
    win_rate = round(100.0 * (ap["ganadas"] or 0) / resueltas, 1) if resueltas else 0.0
    return {
        "usuarios_total": u["n"],
        "saldo_total": u["saldo"],
        "ggr": round((g["apostado"] or 0) - (g["premios"] or 0), 2),
        "por_tipo": por_tipo,
        "top_jugadores": top,
        "apuestas": {**ap, "win_rate": win_rate},
    }
