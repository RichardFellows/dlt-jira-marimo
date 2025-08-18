import marimo

__generated_with = "0.14.17"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    mo.md("# Jira Data Analysis with dlt")
    return mo,


@app.cell
def _(mo):
    mo.md("""
    ## Data Pipeline Setup
    
    This notebook uses dlt (data load tool) to extract data from Jira and analyze it.
    The pipeline loads data into DuckDB for analysis and visualization.
    """)
    return


@app.cell
def _():
    import dlt
    import pandas as pd
    import altair as alt
    from jira_pipeline import jira_source
    return alt, dlt, jira_source, pd


@app.cell
def _():
    # Run the Jira data pipeline
    pipeline = dlt.pipeline(
        pipeline_name='jira_analysis_pipeline',
        destination='duckdb',
        dataset_name='jira_data',
        progress="log",
    )
    return pipeline,


@app.cell
def _(jira_source, pipeline):
    # Load data from Jira
    load_info = pipeline.run(jira_source())
    print("Pipeline execution completed!")
    print(load_info)
    return load_info,


@app.cell
def _(mo):
    mo.md("## Data Exploration")
    return


@app.cell
def _(pipeline):
    # Get the dataset and explore available tables
    dataset = pipeline.dataset()
    tables = list(dataset.schema.data_tables())
    print(f"Available tables: {tables}")
    return dataset, tables


@app.cell
def _(dataset, tables):
    # Show sample data from each table
    sample_data = {}
    for table_name in tables:
        try:
            table = getattr(dataset, table_name)
            df = table.limit(5).df()
            sample_data[table_name] = df
            print(f"\n{table_name.upper()} (first 5 rows):")
            print(df)
        except Exception as e:
            print(f"Error reading {table_name}: {e}")
    
    sample_data
    return df, sample_data, table, table_name


@app.cell
def _(mo):
    mo.md("## Data Visualization")
    return


@app.cell
def _(alt, dataset, tables):
    # Create visualizations based on available data
    charts = []
    
    for table_name in tables:
        try:
            table = getattr(dataset, table_name)
            df = table.df()
            
            if not df.empty:
                # Create a simple count chart
                chart = alt.Chart(df.head(20)).mark_bar().encode(
                    x=alt.X('count():Q', title='Count'),
                    y=alt.Y(f'{df.columns[0]}:N', title=df.columns[0], sort='-x'),
                    tooltip=['count():Q']
                ).properties(
                    title=f'{table_name.title()} Data Distribution',
                    width=400,
                    height=200
                )
                charts.append(chart)
        except Exception as e:
            print(f"Could not create chart for {table_name}: {e}")
    
    charts
    return chart, charts


@app.cell
def _(charts):
    # Display charts
    for i, chart in enumerate(charts):
        print(f"Chart {i+1}:")
        chart.show()
    return i,


@app.cell
def _(mo):
    mo.md("""
    ## Summary
    
    This notebook demonstrates:
    - Loading Jira data using dlt REST API source
    - Storing data in DuckDB for analysis
    - Exploring the loaded data structure
    - Creating basic visualizations with Altair
    
    The pipeline can be extended to include more Jira endpoints and more sophisticated analysis.
    """)
    return


if __name__ == "__main__":
    app.run()
