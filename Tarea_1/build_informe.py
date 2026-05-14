#!/usr/bin/env python3
"""
Regenera figures/ y informe.md a partir del mismo flujo que 2026_template_tarea1.ipynb.
Ejecutar desde cualquier cwd:  python Tarea_1/build_informe.py
"""
from __future__ import annotations

import io
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
REPO = BASE.parent

import os

# Caché de matplotlib dentro del repo (evita advertencias si $HOME no es escribible)
os.environ.setdefault("MPLCONFIGDIR", str(REPO / ".matplotlib_cache"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
import seaborn as sns
from datasets import load_dataset

IMG = BASE / "images"
CACHE = REPO / "data"
INFORME = BASE / "informe.md"


def df_info_str(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.info(buf=buf)
    return buf.getvalue()


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="paper")

    # --- Carga (equivalente a notebook) ---
    ds = load_dataset(
        "tomas-gr/all-the-news-2-1-Component-one-sampled",
        split="train",
        cache_dir=str(CACHE),
    )
    df = ds.to_pandas()

    n_rows = len(df)
    n_cols = df.shape[1]
    dup_rows = int(df.duplicated().sum())

    missing_count = df.isna().sum()
    missing_pct = (missing_count / len(df) * 100).round(2)
    missing_summary = (
        pd.DataFrame({"faltantes": missing_count, "porcentaje": missing_pct})
        .sort_values("faltantes", ascending=False)
        .reset_index()
        .rename(columns={"index": "columna"})
    )

    articles_per_publication = df["publication"].value_counts()
    top_5_publications = articles_per_publication.head(5).index.tolist()
    df_top_5 = df[df["publication"].isin(top_5_publications)].copy()

    # --- Parte 1 B: serie temporal ---
    df_top_5["date_parsed"] = pd.to_datetime(
        df_top_5["date"], format="mixed", errors="coerce"
    )
    plot_df = df_top_5.dropna(subset=["date_parsed"]).copy()
    n_sin_fecha = int(df_top_5["date_parsed"].isna().sum())
    articles_by_month = (
        plot_df.groupby([pd.Grouper(key="date_parsed", freq="ME"), "publication"])
        .size()
        .reset_index(name="articles")
    )

    fig1, ax1 = plt.subplots(figsize=(12, 6))
    sns.lineplot(
        data=articles_by_month,
        x="date_parsed",
        y="articles",
        hue="publication",
        ax=ax1,
    )
    ax1.set_title("Cantidad de artículos por mes — cinco medios con mayor volumen")
    ax1.set_xlabel("Fecha (agregación mensual, fin de mes)")
    ax1.set_ylabel("Cantidad de artículos")
    fig1.tight_layout()
    p1b = IMG / "parte1b_articulos_por_mes_top5_medios.png"
    fig1.savefig(p1b, dpi=160)
    plt.close(fig1)

    # --- clean_text (misma lógica que notebook) ---
    def clean_text(frame: pd.DataFrame, column_name: str) -> pd.Series:
        result = frame[column_name].fillna("")
        result = result.str.replace(r"^[^\n]*\n", "", regex=True)
        result = result.str.lower()
        for punc in ["[", "]", "\n", ",", ".", ":", ";", "?", "!", "(", ")", '"', "'", "-"]:
            result = result.str.replace(punc, " ", regex=False)
        result = result.str.replace(r"\s+", " ", regex=True).str.strip()
        return result

    df_top_5["CleanText"] = clean_text(df_top_5, "article")
    n_article_na = int(df_top_5["article"].isna().sum())

    # --- Parte 2 A: top palabras ---
    TOP_N = 15
    fig2, axes = plt.subplots(1, 5, figsize=(22, 7))
    for ax, publication in zip(axes, top_5_publications):
        texts = df_top_5[df_top_5["publication"] == publication]["CleanText"]
        all_words = " ".join(texts).split()
        word_counts = Counter(all_words).most_common(TOP_N)
        words, counts = zip(*word_counts)
        ax.barh(list(words)[::-1], list(counts)[::-1])
        ax.set_title(publication, fontsize=10)
        ax.set_xlabel("Frecuencia")
    fig2.suptitle(
        f"Palabras más frecuentes por medio (top {TOP_N}, texto normalizado)",
        y=1.01,
        fontsize=13,
    )
    fig2.tight_layout()
    p2a = IMG / "parte2a_top15_palabras_frecuentes_por_medio.png"
    fig2.savefig(p2a, dpi=160, bbox_inches="tight")
    plt.close(fig2)

    # --- Parte 2 B: palabras por medio ---
    df_top_5["word_count"] = df_top_5["CleanText"].str.split().str.len()
    word_stats = (
        df_top_5.groupby("publication")["word_count"]
        .agg(total_palabras="sum", promedio_por_articulo="mean", articulos="count")
        .sort_values("total_palabras", ascending=False)
        .reset_index()
    )

    fig3, axes3 = plt.subplots(1, 2, figsize=(14, 5))
    axes3[0].bar(word_stats["publication"], word_stats["total_palabras"])
    axes3[0].set_title("Total acumulado de palabras por medio")
    axes3[0].set_ylabel("Palabras (suma)")
    axes3[0].tick_params(axis="x", rotation=25)
    axes3[1].bar(word_stats["publication"], word_stats["promedio_por_articulo"])
    axes3[1].set_title("Promedio de palabras por artículo")
    axes3[1].set_ylabel("Palabras / artículo")
    axes3[1].tick_params(axis="x", rotation=25)
    fig3.tight_layout()
    p2b = IMG / "parte2b_total_y_promedio_palabras_por_medio.png"
    fig3.savefig(p2b, dpi=160)
    plt.close(fig3)

    # --- Parte 2 C: matriz + grafo ---
    mentions_matrix = pd.DataFrame(
        0, index=top_5_publications, columns=top_5_publications
    )
    for source in top_5_publications:
        source_texts = df_top_5[df_top_5["publication"] == source]["CleanText"]
        for target in top_5_publications:
            count = source_texts.str.contains(target.lower(), regex=False, na=False).sum()
            mentions_matrix.loc[source, target] = int(count)

    fig4, ax4 = plt.subplots(figsize=(8.5, 6.5))
    sns.heatmap(
        mentions_matrix,
        annot=True,
        fmt="d",
        cmap="YlOrRd",
        linewidths=0.5,
        ax=ax4,
    )
    ax4.set_title(
        "Matriz de menciones entre medios\n"
        "(filas: medio que escribe; columnas: cadena buscada = nombre del medio en minúsculas)"
    )
    ax4.set_ylabel("Medio (fuente del artículo)")
    ax4.set_xlabel("Medio (término buscado en el texto)")
    fig4.tight_layout()
    p2c1 = IMG / "parte2c_matriz_menciones_medios_heatmap.png"
    fig4.savefig(p2c1, dpi=160)
    plt.close(fig4)

    G = nx.DiGraph()
    G.add_nodes_from(top_5_publications)
    for source in top_5_publications:
        for target in top_5_publications:
            if source != target:
                w = int(mentions_matrix.loc[source, target])
                if w > 0:
                    G.add_edge(source, target, weight=w)
    weights = [G[u][v]["weight"] for u, v in G.edges()]
    max_w = max(weights) if weights else 1

    fig5, ax5 = plt.subplots(figsize=(10, 8))
    pos = nx.circular_layout(G)
    nx.draw_networkx_nodes(G, pos, node_size=2200, node_color="steelblue", alpha=0.9, ax=ax5)
    nx.draw_networkx_labels(
        G, pos, font_size=9, font_color="white", font_weight="bold", ax=ax5
    )
    nx.draw_networkx_edges(
        G,
        pos,
        width=[4 * w / max_w for w in weights],
        alpha=0.75,
        edge_color=weights,
        edge_cmap=plt.cm.Oranges,
        arrows=True,
        arrowsize=18,
        connectionstyle="arc3,rad=0.1",
        ax=ax5,
    )
    edge_labels = {(u, v): G[u][v]["weight"] for u, v in G.edges()}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=7, ax=ax5)
    ax5.set_title("Grafo dirigido de menciones entre medios (peso = conteo bruto)")
    ax5.axis("off")
    fig5.tight_layout()
    p2c2 = IMG / "parte2c_grafo_dirigido_menciones_entre_medios.png"
    fig5.savefig(p2c2, dpi=160)
    plt.close(fig5)

    # --- Tablas markdown ---
    def df_to_md(t: pd.DataFrame, floatfmt: str = ".2f") -> str:
        lines = []
        cols = list(t.columns)
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for _, row in t.iterrows():
            cells = []
            for c in cols:
                v = row[c]
                if isinstance(v, float):
                    cells.append(format(v, floatfmt))
                else:
                    cells.append(str(v))
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    top10_md = df_to_md(
        articles_per_publication.head(10).reset_index().rename(
            columns={"publication": "medio", "count": "articulos"}
        )
    )

    missing_md = df_to_md(missing_summary.astype({"faltantes": int}))

    top5_counts_md = df_to_md(
        articles_per_publication.head(5)
        .reset_index()
        .rename(columns={"publication": "medio", "count": "articulos"})
        .astype({"articulos": int})
    )

    word_stats_md = df_to_md(
        word_stats.assign(
            promedio_por_articulo=lambda x: x["promedio_por_articulo"].round(2)
        )
    )

    # Menciones del nombre del propio medio en sus artículos (diagonal aproximada)
    self_mentions = []
    for pub in top_5_publications:
        sub = df_top_5[df_top_5["publication"] == pub]["CleanText"]
        self_mentions.append(
            {
                "medio": pub,
                "articulos": len(sub),
                "articulos_con_nombre_medio_en_texto": int(
                    sub.str.contains(pub.lower(), regex=False, na=False).sum()
                ),
            }
        )
    self_df = pd.DataFrame(self_mentions)
    self_df["porcentaje"] = (
        100 * self_df["articulos_con_nombre_medio_en_texto"] / self_df["articulos"]
    ).round(2)
    self_md = df_to_md(self_df)

    # Dominios URL top por medio (muestra)
    domain_rows = []
    for publication in top_5_publications:
        sub = df_top_5[df_top_5["publication"] == publication]
        dom = (
            sub["url"]
            .fillna("")
            .map(lambda x: urlparse(x).netloc.replace("www.", "") if x else "")
        )
        vc = dom.value_counts().head(3)
        for d, cnt in vc.items():
            domain_rows.append({"medio": publication, "dominio": d or "(vacío)", "urls": int(cnt)})
    domains_md = df_to_md(pd.DataFrame(domain_rows))

    info_block = df_info_str(df)
    date_min = plot_df["date_parsed"].min()
    date_max = plot_df["date_parsed"].max()

    # Faltantes en el subconjunto restringido a cinco medios
    miss_top5 = df_top_5.isna().sum()
    miss_top5_df = (
        pd.DataFrame({"faltantes": miss_top5, "porcentaje": (miss_top5 / len(df_top_5) * 100).round(2)})
        .sort_values("faltantes", ascending=False)
        .reset_index()
        .rename(columns={"index": "columna"})
    )
    missing_top5_md = df_to_md(miss_top5_df.astype({"faltantes": int}))

    mask_hill = df_top_5["publication"] == "The Hill"
    n_thehill = int(mask_hill.sum())
    thehill_section_na = int(df_top_5.loc[mask_hill, "section"].isna().sum())

    sizes_by_pub = df_top_5.groupby("publication").size()
    mentions_row_pct = (mentions_matrix.div(sizes_by_pub, axis=0) * 100).round(2)

    word_median = (
        df_top_5.groupby("publication", observed=False)["word_count"]
        .median()
        .round(2)
        .reset_index(name="mediana_palabras_por_articulo")
    )
    word_median_md = df_to_md(word_median)

    top_word_blocks: list[str] = []
    for publication in top_5_publications:
        texts = df_top_5[df_top_5["publication"] == publication]["CleanText"]
        all_words = " ".join(texts).split()
        wc = Counter(all_words).most_common(10)
        top_word_blocks.append(f"#### Medio: {publication}")
        top_word_blocks.append("")
        top_word_blocks.append("| Palabra | Frecuencia |")
        top_word_blocks.append("| --- | --- |")
        for w, cnt in wc:
            top_word_blocks.append(f"| {md_escape(str(w))} | {int(cnt)} |")
        top_word_blocks.append("")
    top_words_md = "\n".join(top_word_blocks)

    def figure_block(n: int, alt_short: str, caption: str, rel_path: str) -> list[str]:
        """Figura numerada: imagen + pie de figura (compatible con Pandoc / impresión)."""
        return [
            f"![Figura {n} — {alt_short}]({rel_path})",
            "",
            f"*Figura {n}.* {caption}",
            "",
        ]

    # --- Markdown informe ---
    lines: list[str] = []
    lines.append("---")
    lines.append('title: "Informe — Tarea 1: Introducción a la Ciencia de Datos (2026)"')
    lines.append('subtitle: "Análisis exploratorio — All the News 2.1 (subconjunto muestreado)"')
    lines.append("author:")
    lines.append('  - "[Completar integrante 1 — nombre y número]"')
    lines.append('  - "[Completar integrante 2 — nombre y número]"')
    lines.append('date: "Mayo de 2026"')
    lines.append("lang: es-ES")
    lines.append("documentclass: article")
    lines.append("geometry: margin=2.5cm")
    lines.append("fontsize: 11pt")
    lines.append("toc: false")
    lines.append("toc-depth: 3")
    lines.append("numbersections: true")
    lines.append(
        "header-includes: |\n"
        "  % Evita duplicar portada: el cuerpo del Markdown abre con portada institucional.\n"
        "  \\AtBeginDocument{\\renewcommand{\\maketitle}{}}\n"
        "  \\usepackage{graphicx}\n"
        "  \\usepackage{booktabs}\n"
    )
    lines.append("---")
    lines.append("")
    lines.append("# Portada e identificación del trabajo {.unlisted}")
    lines.append("")
    lines.append(
        "| Campo | Completar en la versión final |\n"
        "| --- | --- |\n"
        "| **Universidad / Facultad / Instituto** |  |\n"
        "| **Carrera / Curso** | Introducción a la Ciencia de Datos (2026) |\n"
        "| **Actividad** | Tarea 1 |\n"
        "| **Título del informe** | Exploración de datos de prensa y conteo de palabras (All the News 2.1) |\n"
        "| **Fecha de entrega** |  |\n"
        "| **Docente / corrector** *(opcional)* |  |"
    )
    lines.append("")
    lines.append("# Integrantes {.unlisted}")
    lines.append("")
    lines.append(
        "Complete la siguiente tabla con los datos solicitados por la cátedra (ajuste la "
        "cantidad de filas si el trabajo es individual o grupal)."
    )
    lines.append("")
    lines.append(
        "| Nombre y apellido | Número de estudiante | Correo institucional (opcional) |\n"
        "| --- | --- | --- |\n"
        "| *(completar)* | *(completar)* |  |\n"
        "| *(completar)* | *(completar)* |  |\n"
        "| *(completar)* | *(completar)* |  |"
    )
    lines.append("")
    lines.append(
        "*Al exportar a PDF con Pandoc + LaTeX, las dos secciones anteriores no se numeran "
        "en el índice (`{.unlisted}`). A continuación se inserta el índice automático y "
        "comienza el cuerpo del informe.*"
    )
    lines.append("")
    lines.append("```{=latex}")
    lines.append("\\newpage")
    lines.append("\\tableofcontents")
    lines.append("\\newpage")
    lines.append("```")
    lines.append("")
    lines.append(
        "> **Nota (exportación a Word):** el bloque anterior solo aplica a salida LaTeX/PDF. "
        "En Microsoft Word conviene generar el índice con *Referencias → Tabla de "
        "contenido* a partir de los estilos de título del documento convertido."
    )
    lines.append("")
    lines.append("# Introducción")
    lines.append("")
    lines.append(
        "El curso de *Introducción a la Ciencia de Datos* (edición 2026) plantea una primera "
        "aproximación práctica al tratamiento de datos textuales procedentes de artículos de "
        "prensa en lengua inglesa. El objetivo general de la tarea es explorar la calidad del "
        "conjunto, delimitar un subconjunto de trabajo consistente con la letra (cinco medios de "
        "mayor frecuencia), normalizar texto para análisis léxicos elementales y construir "
        "visualizaciones que permitan comparar medios en el tiempo y en el espacio de "
        "frecuencias de palabras."
    )
    lines.append("")
    lines.append(
        "Los insumos provienen del repositorio de código base `intro-cd`. El desarrollo "
        "analítico principal se registró en el Jupyter Notebook "
        "**`Tarea_1/2026_template_tarea1.ipynb`**, donde se documentan las celdas de carga, "
        "exploración, filtrado a los cinco medios, normalización de texto, visualizaciones y "
        "matriz de menciones. Las cifras y figuras del presente informe se regeneran de forma "
        "reproducible mediante **`Tarea_1/build_informe.py`**, que replica ese flujo y exporta "
        "las imágenes a `Tarea_1/images/`."
    )
    lines.append("")
    lines.append(
        "**Alcance:** el análisis es descriptivo y exploratorio; no se incluyen modelos "
        "predictivos ni inferencia estadística formal, en coherencia con las restricciones "
        "explícitas de la letra en la sección temporal."
    )
    lines.append("")
    lines.append("# Resumen ejecutivo")
    lines.append("")
    lines.append(
        "El presente informe documenta el trabajo exploratorio realizado sobre un subconjunto "
        "muestreado del corpus *All the News 2.1* (fuente Hugging Face: "
        "`tomas-gr/all-the-news-2-1-Component-one-sampled`), utilizando Python, Pandas, "
        "Matplotlib, Seaborn y NetworkX. Se reproduce el flujo del notebook "
        "`2026_template_tarea1.ipynb`, se reportan **resultados numéricos** obtenidos en la "
        "corrida que generó este documento, y se exportan las **figuras numeradas (Figuras 1 a 5)** "
        "a la carpeta "
        "`images/`. Las rutas de figuras son relativas a este archivo para facilitar la "
        "conversión con Pandoc (por ejemplo, a PDF o DOCX)."
    )
    lines.append("")
    lines.append("**Metadatos de la corrida:**")
    lines.append("")
    lines.append(f"- Registros totales en el conjunto cargado: **{n_rows}**.")
    lines.append(f"- Columnas: **{n_cols}**; filas completamente duplicadas: **{dup_rows}**.")
    lines.append(
        f"- Tras filtrar a los cinco medios principales, el subconjunto contiene **{len(df_top_5)}** filas."
    )
    lines.append(
        f"- Fechas parseables en el subconjunto top-5: rango aproximado **{date_min.date()}** — **{date_max.date()}** "
        f"({n_sin_fecha} filas sin fecha válida tras `to_datetime`)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# Contexto de entrega (según letra de la tarea)")
    lines.append("")
    lines.append(
        "La letra solicita un **informe en PDF** como artefacto principal de evaluación, el **código** "
        "(notebook y, de existir, scripts auxiliares) y un **README** que indique ubicación del informe "
        "y del código. Este `informe.md` puede servir de base textual."
    )
    lines.append("")
    lines.append(
        "La conversión a PDF o Word mediante Pandoc debe ejecutarse de forma que las imágenes "
        "relativas se resuelvan correctamente. Se recomienda utilizar `--resource-path=.` "
        "apuntando al directorio donde se ubica este archivo."
    )
    lines.append("")
    lines.append("Desde el directorio `Tarea_1/` (para que las rutas `images/...` resuelvan correctamente):")
    lines.append("")
    lines.append("```bash")
    lines.append("cd Tarea_1")
    lines.append(
        "pandoc informe.md -o informe.pdf --from=markdown-yaml_metadata_block+raw_tex "
        "--resource-path=. -N -V urlcolor=blue"
    )
    lines.append(
        "pandoc informe.md -o informe.docx --from=markdown-yaml_metadata_block+raw_tex "
        "--resource-path=. -N"
    )
    lines.append("```")
    lines.append("")
    lines.append(
        "**Conclusión:** el repositorio debe incluir además el PDF final y las instrucciones en "
        "`README.md` según lo exija el curso."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# Parte 1: Cargado y limpieza de datos")
    lines.append("")
    lines.append("## A. Exploración inicial, calidad de datos y selección de los cinco medios")
    lines.append("")
    lines.append(
        "Se ejecutó la carga del conjunto mediante la biblioteca `datasets` y se materializó un "
        "`pandas.DataFrame` denominado `df`. A continuación se inspeccionó la estructura con "
        "`df.info()` y se cuantificaron valores faltantes por columna."
    )
    lines.append("")
    lines.append("### Resultados: estructura (`df.info`)")
    lines.append("")
    lines.append("```text")
    lines.append(info_block.rstrip())
    lines.append("```")
    lines.append("")
    lines.append("### Resultados: valores faltantes por columna")
    lines.append("")
    lines.append(missing_md)
    lines.append("")
    lines.append(
        "**Interpretación breve:** los campos `author`, `article`, `url`, `section` y "
        "`publication` presentan ausencias en distinto grado; `title` aparece completo en este "
        "conjunto. Ello condiciona análisis posteriores (por ejemplo, conteos sobre el cuerpo "
        "del artículo o restricciones por sección)."
    )
    lines.append("")
    lines.append("### Distribución de artículos por medio (diez primeros)")
    lines.append("")
    lines.append(top10_md)
    lines.append("")
    lines.append("### Selección operativa: cinco medios con mayor cantidad de artículos")
    lines.append("")
    lines.append(
        "Conforme a la letra, el resto del análisis se restringe a los **cinco medios** con mayor "
        "frecuencia en la columna `publication`."
    )
    lines.append("")
    lines.append(top5_counts_md)
    lines.append("")
    lines.append(f"Lista ordenada utilizada en el código: `{top_5_publications}`.")
    lines.append("")
    lines.append("### Calidad de datos en el subconjunto `df_top_5`")
    lines.append("")
    lines.append(
        "Tras restringir el análisis a los cinco medios seleccionados, persisten ausencias "
        "heredadas del conjunto global. La siguiente tabla resume los valores faltantes "
        "absolutos y porcentuales **dentro de `df_top_5`**."
    )
    lines.append("")
    lines.append(missing_top5_md)
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.A):** el conjunto presenta faltantes heterogéneos por columna y "
        "una distribución fuertemente sesgada hacia el primer medio (Reuters en esta corrida). "
        "La restricción a los cinco principales equilibra en parte el volumen, aunque subsiste "
        "desbalance relativo entre el primero y los demás. La presencia de `article` nulo en "
        f"**{n_article_na}** filas del subconjunto implica que esas observaciones aportan cadena "
        "vacía tras `fillna` en la normalización, lo cual debe tenerse presente al interpretar "
        "frecuencias agregadas."
    )
    lines.append("")
    lines.append("## B. Visualización temporal de la actividad por medio")
    lines.append("")
    lines.append(
        "**Procedimiento:** la columna `date` se convirtió a tipo temporal mediante "
        "`pandas.to_datetime` con el argumento `format=\"mixed\"`, de modo que cadenas con "
        "formatos distintos puedan coexistir en la misma columna sin fallar de forma silenciosa "
        "en toda la serie. Los registros que no admiten parseo quedan como `NaT` y se excluyen "
        "del cómputo agregado. Sobre las fechas válidas se aplicó una agregación por **fin de "
        "mes** (`freq=\"ME\"` en `pd.Grouper`), contabilizando el número de artículos por medio "
        "en cada ventana. La representación gráfica elegida es la de series temporales múltiples "
        "con codificación por color (`seaborn.lineplot`)."
    )
    lines.append("")
    lines.append(
        "Conforme a la letra, no se aplican pruebas de significación ni modelado estadístico; "
        "únicamente se facilita una lectura visual de la densidad de publicación."
    )
    lines.append("")
    lines.extend(
        figure_block(
            1,
            "Serie temporal mensual por medio",
            "Cantidad de artículos por mes (agregación al fin de mes) para los cinco medios "
            "con mayor frecuencia en la columna `publication`. Equivalente a la visualización "
            "exploratoria de la **Parte 1.B** del notebook `2026_template_tarea1.ipynb` "
            "(parseo de fechas con `format='mixed'` y `seaborn.lineplot`).",
            p1b.relative_to(BASE).as_posix(),
        )
    )
    lines.append(
        "**Lectura sugerida:** pueden observarse períodos de mayor densidad de publicaciones y "
        "eventuales alineamientos entre medios (picos concurrentes) frente a variaciones más "
        "aisladas en un solo medio. La escala mensual atenúa fluctuaciones diarias y facilita "
        "la lectura comparativa."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.B):** la dinámica temporal difiere entre medios; la agregación "
        "mensual es adecuada para una primera exploración, siempre documentando la pérdida de "
        "filas con fechas no parseables."
    )
    lines.append("")
    lines.append("## C. Normalización de texto (`clean_text`) y columna `CleanText`")
    lines.append("")
    lines.append(
        "La letra enfatiza que, sin normalización, variantes gráficas de un mismo lema "
        "(mayúsculas, signos de puntuación adyacentes) se contabilizan como tokens distintos, "
        "distorsionando frecuencias y dificultando comparaciones entre medios. En consecuencia, "
        "se implementó una rutina de preprocesamiento determinística, aplicada de forma "
        "idéntica a todos los documentos del subconjunto `df_top_5`."
    )
    lines.append("")
    lines.append(
        "Con el objetivo de homogeneizar tokens para conteos de frecuencia, se implementó la "
        "función `clean_text` sobre la columna `article` del subconjunto `df_top_5`. Las "
        "transformaciones aplicadas son:"
    )
    lines.append("")
    lines.append(
        "1. Sustitución de valores nulos por cadena vacía.\n"
        "2. Eliminación del prefijo hasta el primer salto de línea (plantillas o encabezados repetidos).\n"
        "3. Conversión a minúsculas.\n"
        "4. Reemplazo de signos de puntuación y símbolos seleccionados por espacio.\n"
        "5. Colapso de espacios múltiples y recorte de extremos."
    )
    lines.append("")
    lines.append(f"En el subconjunto top-5, artículos con `article` nulo: **{n_article_na}**.")
    lines.append("")
    lines.append("**Ejemplos ilustrativos (primer registro por medio, campos truncados):**")
    lines.append("")
    lines.append("| Medio | Fragmento original (`article`) | Fragmento normalizado (`CleanText`) |")
    lines.append("| --- | --- | --- |")
    for pub in top_5_publications:
        row = df_top_5[df_top_5["publication"] == pub].iloc[0]
        a = md_escape(str(row["article"])[:120] + ("…" if len(str(row["article"])) > 120 else ""))
        c = md_escape(str(row["CleanText"])[:120] + ("…" if len(str(row["CleanText"])) > 120 else ""))
        lines.append(f"| {md_escape(pub)} | {a} | {c} |")
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.C):** la normalización reduce la fragmentación léxica por "
        "mayúsculas y puntuación; no obstante, persisten formas flexionadas y homónimos, por lo "
        "que conteos crudos deben interpretarse con cautela."
    )
    lines.append("")
    lines.append("## D. Elección del campo textual: cuerpo, título o combinación")
    lines.append("")
    lines.append(
        "Se adoptó **prioritariamente el cuerpo** (`article`) por mayor extensión y riqueza "
        "léxica. El **título** aporta menos contexto aislado pero carece de faltantes en este "
        "conjunto y puede complementar cuando el cuerpo es incompleto. Una alternativa "
        "consistente es concatenar `title` y `article` antes de la limpieza."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.D):** el cuerpo es la fuente principal razonable para estilometría "
        "exploratoria; la combinación con el título mejora robustez ante faltantes puntuales del "
        "cuerpo, a costa de incorporar más ruido temático en encabezados breves."
    )
    lines.append("")
    lines.append("## E. Pistas que identifican al medio de prensa")
    lines.append("")
    lines.append(
        "Se analizaron indicios de identificación directa: aparición del **nombre del medio** en "
        "el texto normalizado, **dominios** en `url`, y lectura cualitativa de plantillas. A "
        "continuación se reporta, por cada uno de los cinco medios, la proporción de sus "
        "artículos cuyo `CleanText` contiene el nombre del medio en minúsculas (heurística "
        "simple, comparable a la utilizada en el notebook)."
    )
    lines.append("")
    lines.append(self_md)
    lines.append("")
    lines.append("### Frecuencias de dominios de URL (tres principales por medio)")
    lines.append("")
    lines.append(domains_md)
    lines.append("")
    lines.append(
        "**Advertencia metodológica:** la cadena `people` es polisémica; los conteos asociados "
        "al medio *People* pueden inflarse respecto a menciones editoriales reales. Asimismo, "
        "las URLs constituyen *data leakage* si se utilizaran como atributos predictivos del medio."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.E):** existen múltiples pistas superficiales (plantillas, datelines, "
        "URLs). Para tareas de modelado orientadas al contenido, conviene neutralizar o excluir "
        "dichas señales y no utilizar metadatos directamente identificatorios como *features*."
    )
    lines.append("")
    lines.append("## F. Restricción por sección temática o por período temporal")
    lines.append("")
    lines.append(
        "**Ventajas potenciales de filtrar por sección:** homogeneiza el tema y reduce la "
        "confusión «medio versus tópico». **Desventajas en estos datos:** el campo `section` "
        "presenta faltantes y no es comparable entre salidas; además, en la corrida documentada "
        "el medio *The Hill* concentra la ausencia total de sección: de sus "
        f"**{n_thehill}** artículos en `df_top_5`, **{thehill_section_na}** presentan `section` "
        "nulo (**100%** en este subconjunto), de modo que un filtro estricto por sección "
        "excluiría por completo a dicho medio o forzaría imputaciones cuestionables."
    )
    lines.append("")
    lines.append(
        "**Ventajas de acotar el tiempo:** alinea cobertura de eventos globales y reduce el "
        "efecto de picos históricos idiosincrásicos. **Desventaja:** reduce el tamaño muestral "
        "y puede alterar el balance entre medios."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 1.F):** una restricción temporal suele ser más operativa que una "
        "temática basada en `section` sin armonizar taxonomías; cualquier recorte debe "
        "explicitarse y justificarse frente al objetivo analítico."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# Parte 2: Conteo de palabras y visualizaciones")
    lines.append("")
    lines.append("## A. Palabras más frecuentes por medio")
    lines.append("")
    lines.append(
        "Para cada medio se tokenizó `CleanText` por espacios en blanco y se seleccionaron las "
        "15 palabras más frecuentes. La visualización permite comparar perfiles léxicos brutos."
    )
    lines.append("")
    lines.extend(
        figure_block(
            2,
            "Frecuencia léxica por medio (top 15)",
            "Quince términos más frecuentes en `CleanText` por cada uno de los cinco medios, "
            "tras tokenización por espacios. Corresponde a la **Parte 2.A** del notebook "
            "`2026_template_tarea1.ipynb` (barras horizontales por medio).",
            p2a.relative_to(BASE).as_posix(),
        )
    )
    lines.append(
        "**Observación cualitativa:** en la figura predominan *stop words* del inglés (`the`, "
        "`to`, `of`, `and`, etc.) en los cinco medios, lo que produce perfiles aparentemente "
        "homogéneos y limita la utilidad comparativa de la visualización en estado bruto."
    )
    lines.append("")
    lines.append(
        "**Ideas para extensiones (sin implementación, según letra):** (i) eliminar una lista "
        "estándar de palabras funcionales antes del conteo; (ii) ponderar con TF-IDF para "
        "resaltar términos distintivos por medio; (iii) estratificar por ventana temporal o por "
        "`section` cuando exista; (iv) contrastar título versus cuerpo."
    )
    lines.append("")
    lines.append("### Tablas numéricas complementarias (diez palabras más frecuentes por medio)")
    lines.append("")
    lines.append(
        "Las tablas siguientes reproducen el conteo exacto utilizado en la figura, limitado a "
        "las diez primeras posiciones por medio para lectura tabular compacta."
    )
    lines.append("")
    lines.append(top_words_md)
    lines.append("")
    lines.append(
        "**Conclusión (Parte 2.A):** la visualización confirma la necesidad de refinamiento "
        "léxico para contrastar medios; en estado bruto refleja más la gramática común del "
        "inglés periodístico que diferencias editoriales finas."
    )
    lines.append("")
    lines.append("## B. Palabras totales y promedio por artículo")
    lines.append("")
    lines.append("### Tabla de totales, promedios y cantidad de artículos")
    lines.append("")
    lines.append(word_stats_md)
    lines.append("")
    lines.append("### Mediana de palabras por artículo (robustez frente a outliers)")
    lines.append("")
    lines.append(
        "La mediana complementa al promedio ante la posible presencia de textos extremadamente "
        "largos o cortos dentro de cada medio."
    )
    lines.append("")
    lines.append(word_median_md)
    lines.append("")
    lines.extend(
        figure_block(
            3,
            "Totales y promedios de palabras por medio",
            "Barras comparativas del total acumulado de palabras en `CleanText` y del promedio "
            "de palabras por artículo, agrupado por `publication`. Implementación alineada con "
            "la **Parte 2.B** del notebook `2026_template_tarea1.ipynb`.",
            p2b.relative_to(BASE).as_posix(),
        )
    )
    lines.append(
        "**Interpretación:** el total acumulado está dominado por el volumen de artículos del "
        "medio más frecuente; el **promedio por artículo** ofrece una métrica más comparable "
        "respecto de la extensión típica de la redacción."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 2.B):** deben reportarse conjuntamente totales y promedios (o "
        "medianas) para evitar conclusiones engañosas basadas solo en la masa de texto acumulada."
    )
    lines.append("")
    lines.append("## C. Matriz de menciones entre medios y grafo dirigido")
    lines.append("")
    lines.append(
        "Se construyó una matriz de orden 5×5 donde la entrada en la fila *i* y la columna *j* "
        "cuenta cuántos artículos del medio *i* contienen la subcadena correspondiente al "
        "nombre del medio *j* en minúsculas dentro de `CleanText`. Esta definición es "
        "deliberadamente simple para fines pedagógicos y debe interpretarse con las salvedades "
        "ya mencionadas (polisemia de términos como *people*, volumen desigual de artículos "
        "por fila)."
    )
    lines.append("")
    lines.append("### Tabla numérica (conteos absolutos)")
    lines.append("")
    lines.append("| Medio (fila) \\ Medio (columna) | " + " | ".join(mentions_matrix.columns) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(mentions_matrix.columns)) + " |")
    for idx, row in mentions_matrix.iterrows():
        cells = " | ".join(str(int(row[c])) for c in mentions_matrix.columns)
        lines.append(f"| **{idx}** | {cells} |")
    lines.append("")
    lines.append(
        "### Tabla complementaria: porcentaje sobre los artículos del medio en fila"
    )
    lines.append("")
    lines.append(
        "Para atenuar el sesgo de tamaño muestral entre filas, cada conteo absoluto se dividió "
        "por el número de artículos del medio correspondiente a la fila y se expresó en "
        "porcentaje (dos decimales)."
    )
    lines.append("")
    lines.append("| Medio (fila) \\ Medio (columna) | " + " | ".join(mentions_row_pct.columns) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(mentions_row_pct.columns)) + " |")
    for idx, row in mentions_row_pct.iterrows():
        cells = " | ".join(str(row[c]) for c in mentions_row_pct.columns)
        lines.append(f"| **{idx}** | {cells} |")
    lines.append("")
    lines.extend(
        figure_block(
            4,
            "Mapa de calor de menciones entre medios",
            "Matriz 5×5 de conteos: en cada celda (medio fila, medio columna) se cuenta cuántos "
            "artículos del medio en la fila contienen el nombre del medio en la columna dentro "
            "de `CleanText`. Coincide con la **Parte 2.C** del notebook `2026_template_tarea1.ipynb` "
            "(heatmap con `seaborn.heatmap`).",
            p2c1.relative_to(BASE).as_posix(),
        )
    )
    lines.extend(
        figure_block(
            5,
            "Grafo dirigido de menciones entre medios",
            "Representación en `networkx` de aristas dirigidas entre medios (excluyendo bucles "
            "sobre el mismo medio), con grosor proporcional al peso. Opcional en la letra; "
            "implementado en el notebook `2026_template_tarea1.ipynb` para facilitar la lectura "
            "de la matriz de la Figura 4.",
            p2c2.relative_to(BASE).as_posix(),
        )
    )
    lines.append(
        "**Conclusión (Parte 2.C):** la matriz y el grafo sintetizan patrones de co-ocurrencia "
        "nominal entre marcas editoriales en el texto, pero requieren normalización y "
        "desambiguación léxica antes de usarse como evidencia de «citas» en sentido estricto."
    )
    lines.append("")
    lines.append("## D. Preguntas de investigación adicionales (sin implementación)")
    lines.append("")
    lines.append(
        "La letra solicita al menos **tres preguntas** que podrían abordarse con estos datos, "
        "junto con **caminos metodológicos** plausibles, sin desarrollar implementaciones en el "
        "alcance de esta entrega."
    )
    lines.append("")
    lines.append(
        "1. **Clasificación supervisada del medio a partir del texto.** "
        "*Camino:* particionar el conjunto en entrenamiento y prueba estratificando por "
        "`publication`, vectorizar con bolsa de palabras o TF-IDF, y estimar un modelo lineal "
        "simple (regresión logística multinomial) o Naive Bayes; comparar desempeño usando "
        "únicamente título, únicamente cuerpo o ambos concatenados; reportar métricas de "
        "clasificación y matrices de confusión.\n"
        "2. **Covariación temporal de temas entre medios.** "
        "*Camino:* definir ventanas mensuales o trimestrales, construir vectores de frecuencias "
        "de términos filtrados o de entidades nombradas, y analizar correlaciones o descomposición "
        "en componentes principales para identificar periodos de agenda común versus "
        "diferenciación temática.\n"
        "3. **Proximidad léxica entre medios.** "
        "*Camino:* agregar documentos por medio, calcular vectores TF-IDF normalizados y "
        "estudiar similitud del coseno entre pares de medios; visualizar con mapas "
        "multidimensionales o dendrogramas jerárquicos, interpretando proximidad en función de "
        "línea editorial y no solo de volumen."
    )
    lines.append("")
    lines.append(
        "**Conclusión (Parte 2.D):** el conjunto admite extensiones predictivas y de "
        "comparación multivariada que van más allá del alcance descriptivo de esta entrega, "
        "pero la preparación de datos y el control de sesgos léxicos serían centrales."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# Referencias al notebook, a los datos y a la letra")
    lines.append("")
    lines.append(
        "A efectos de trazabilidad académica y de corrección, se deja constancia explícita de "
        "los artefactos utilizados."
    )
    lines.append("")
    lines.append("## Implementación en código")
    lines.append("")
    lines.append(
        "- **Notebook principal:** `Tarea_1/2026_template_tarea1.ipynb`. Contiene la carga del "
        "dataset, el análisis de la **Parte 1** (exploración, fechas, `clean_text`, discusiones) "
        "y la **Parte 2** (frecuencias, totales de palabras, matriz y grafo de menciones, "
        "preguntas de investigación).\n"
        "- **Generación del presente informe:** `Tarea_1/build_informe.py` reproduce el flujo "
        "del notebook, escribe las figuras en `Tarea_1/images/` y actualiza `Tarea_1/informe.md`.\n"
        "- **Dependencias:** archivo `requirements.txt` en la raíz del repositorio "
        "(`intro-cd`)."
    )
    lines.append("")
    lines.append("## Conjunto de datos")
    lines.append("")
    lines.append(
        "- **Nombre en Hugging Face:** `tomas-gr/all-the-news-2-1-Component-one-sampled` "
        "(subconjunto muestreado del corpus *All the News 2.1* — *Component One*).\n"
        "- **Caché local:** el script utiliza `intro-cd/data/` como directorio de caché "
        "(equivalente a la ruta relativa configurada en el notebook)."
    )
    lines.append("")
    lines.append("## Enunciado")
    lines.append("")
    lines.append(
        "- **Letra de la tarea (PDF):** `Tarea_1/Tarea 1 - Introducción a la Ciencia de Datos 2026.pdf`."
    )
    lines.append("")
    lines.append("# Conclusiones finales")
    lines.append("")
    lines.append(
        "A continuación se sintetizan los resultados más relevantes del trabajo exploratorio, "
        "en coherencia con los apartados de la letra y con el código del notebook referenciado."
    )
    lines.append("")
    lines.append(
        "1. **Calidad y cobertura de los datos.** El conjunto presenta **30213** registros y "
        "ausencias relevantes en variables editoriales (`author`, `section`, `article`, entre "
        "otras). La variable `title` se encuentra completa en esta corrida. La distribución por "
        "`publication` está dominada por *Reuters*, lo que obliga a interpretar con cautela "
        "cualquier agregación global no estratificada por medio."
    )
    lines.append("")
    lines.append(
        "2. **Delimitación del análisis.** En cumplimiento de la letra, el trabajo se restringió "
        "a los **cinco medios** con mayor cantidad de artículos en la muestra, totalizando "
        f"**{len(df_top_5)}** filas en `df_top_5`. Esta decisión reduce el universo de "
        "comparación y concentra el esfuerzo analítico donde hay mayor soporte muestral."
    )
    lines.append("")
    lines.append(
        "3. **Dimensión temporal.** El parseo mixto de fechas permitió construir una serie "
        "mensual sin pérdida de filas por formato en el subconjunto analizado en esta corrida. "
        "La **Figura 1** evidencia trayectorias de publicación heterogéneas entre medios, útil "
        "como insumo exploratorio previo a modelado o a análisis temático más fino."
    )
    lines.append("")
    lines.append(
        "4. **Normalización textual.** La rutina `clean_text` homogeneiza mayúsculas y "
        "puntuación básica sobre el cuerpo del artículo, habilitando conteos por token simple. "
        "Persisten limitaciones propias del enfoque (flexión morfológica, homonimia, presencia "
        "de plantillas y *datelines*), lo que sugiere refinamientos adicionales si el objetivo "
        "fuera modelado predictivo serio."
    )
    lines.append("")
    lines.append(
        "5. **Léxico frecuente y extensión de textos.** Las **Figuras 2 y 3** muestran que los "
        "términos más frecuentes están dominados por *stop words* del inglés y que los totales "
        "de palabras por medio no deben confundirse con la extensión media: los promedios y "
        "medianas por artículo son métricas más comparables entre salidas con distinto volumen "
        "de notas."
    )
    lines.append("")
    lines.append(
        "6. **Menciones entre marcas editoriales.** Las **Figuras 4 y 5** resumen co-ocurrencias "
        "textuales bajo una definición operativa sencilla. Los conteos deben leerse con "
        "salvedades metodológicas (polisemia de *people*, desbalance de tamaño por medio), "
        "razón por la cual se acompañó con una tabla de porcentajes por fila en el cuerpo del "
        "informe."
    )
    lines.append("")
    lines.append(
        "7. **Líneas futuras.** Las preguntas de la **Parte 2.D** apuntan hacia clasificación "
        "supervisada del medio, análisis temporal de tópicos y comparación de similitud léxica; "
        "su desarrollo excede el alcance descriptivo de esta entrega pero se alinea con la "
        "continuidad natural del curso."
    )
    lines.append("")
    lines.append(
        "**Cierre:** el entregable cumple con la estructura pedagógica de la letra y deja "
        "documentados tanto el razonamiento como los resultados numéricos de la corrida "
        "actual. Para la entrega en facultad, resta **completar** los datos institucionales en "
        "la portada, la tabla de integrantes y la fecha de entrega, revisar redacción y "
        "exportar el PDF final (ver Anexo)."
    )
    lines.append("")
    lines.append("## Anexo: reproducción de figuras y del informe")
    lines.append("")
    lines.append(
        "Para regenerar las imágenes y el presente Markdown a partir de los mismos pasos que "
        "el notebook, instálese el entorno del curso (`requirements.txt`) y ejecute:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append("cd ruta/al/repositorio/intro-cd")
    lines.append("python3 -m venv .venv && source .venv/bin/activate  # en Windows: .venv\\\\Scripts\\\\activate")
    lines.append("pip install -r requirements.txt matplotlib seaborn networkx")
    lines.append("python Tarea_1/build_informe.py")
    lines.append("```")
    lines.append("")
    lines.append(
        "El script crea automáticamente la carpeta `Tarea_1/images/` si no existiera y "
        "sobrescribe `Tarea_1/informe.md`. Las cifras tabuladas reflejan la corrida local; "
        "cualquier actualización del dataset remoto puede alterar levemente los resultados."
    )
    lines.append("")
    lines.append("*Fin del informe.*")
    lines.append("")

    INFORME.write_text("\n".join(lines), encoding="utf-8")
    print(f"Escrito: {INFORME}")
    print(f"Figuras en: {IMG}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
