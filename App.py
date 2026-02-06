import streamlit as st
import pandas as pd
from pulp import *
from io import BytesIO
import time
import math  # Importamos math globalmente

# -----------------------------------------------------------------------------
# 1. CONFIGURACIÓN E IMPORTACIONES
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Modelo de Sacrificio de Reses", layout="wide")
st.title("Optimización de Sacrificio de Reses")

# -----------------------------------------------------------------------------
# 2. FUNCIONES DE ESTILO Y FORMATO
# -----------------------------------------------------------------------------
def aplicar_estilos_financiera(df):
    """Aplica estilos condicionales a la tabla financiera."""
    styles = pd.DataFrame('', index=df.index, columns=df.columns)
    if 'Concepto' not in df.columns or df.empty:
        return styles

    for idx, row in df.iterrows():
        concepto = str(row['Concepto'])
        estilo_fila = ''
        
        if 'SUBTOTAL' in concepto:
            estilo_fila = 'font-weight: bold; background-color: #f0f0f0; color: black'
        elif 'Costo' in concepto and 'Ingreso' not in concepto:
            estilo_fila = 'color: #d62728'  # Rojo
        elif 'Ingreso' in concepto:
            estilo_fila = 'color: #2ca02c'  # Verde
            
        if estilo_fila:
            styles.loc[idx, :] = estilo_fila
            
        if ('Costo' in concepto or 'Ingreso' in concepto) and 'SUBTOTAL' not in concepto:
            styles.loc[idx, 'Concepto'] = f"{estilo_fila}; font-weight: bold"

    return styles

def mostrar_dataframe_con_estilos(df, height=400):
    try:
        st.dataframe(df.style.apply(aplicar_estilos_financiera, axis=None), use_container_width=True, height=height)
    except Exception as e:
        st.dataframe(df, use_container_width=True, height=height)

# -----------------------------------------------------------------------------
# 3. FUNCIONES DE PROCESAMIENTO DE DATOS
# -----------------------------------------------------------------------------
def procesar_archivo(uploaded_file):
    try:
        excel_data = pd.ExcelFile(uploaded_file)
        dfs = {}
        for sheet_name in excel_data.sheet_names:
            dfs[sheet_name] = pd.read_excel(excel_data, sheet_name=sheet_name)
        return dfs
    except Exception as e:
        st.error(f"Error al leer el archivo Excel: {str(e)}")
        return None

def crear_diccionario(df, columnas_clave, columna_valor):
    diccionario = {}
    for index, row in df.iterrows():
        if len(columnas_clave) == 1:
            clave = row[columnas_clave[0]]
        else:
            clave = tuple(row[col] for col in columnas_clave)
        valor = row[columna_valor]
        diccionario[clave] = valor
    return diccionario

def crear_diccionario_rdto_total(inputs_opt_res):
    # Obtener DataFrames
    df_merma_tte = inputs_opt_res['MermaTTE'].copy()
    df_mermas_plantas = inputs_opt_res['MermasPlantas'].copy()
    
    # Fusionar los DataFrames
    df_merged = pd.merge(
        df_merma_tte,
        df_mermas_plantas[['PLANTA', 'M_CANAL_CALIENTE', 'M_CANAL_FRIO']],
        on='PLANTA',
        how='left'
    )
    
    # Calcular rendimiento total
    df_merged['RENDIMIENTO'] = 1 - (
        (1 - df_merged['MERMA']) * (1 - df_merged['M_CANAL_CALIENTE'].fillna(0)) * (1 - df_merged['M_CANAL_FRIO'].fillna(0))
    )
    
    # Crear diccionario
    rdto_total = dict(zip(
        zip(df_merged['ZONA'], df_merged['PLANTA']),
        df_merged['RENDIMIENTO']
    ))
    
    return rdto_total

# -----------------------------------------------------------------------------
# 4. FUNCIÓN PRINCIPAL DEL MODELO DE OPTIMIZACIÓN
# -----------------------------------------------------------------------------
def ejecutar_modelo(inputs_opt_res, valor_kg, MinCompra):
    try:
        # Conjuntos
        Zona = list(set(inputs_opt_res['Oferta']['ZONA']))
        Planta_S = list(set(inputs_opt_res['CV_PDN']['PLANTA']))
        Semana = list(set(inputs_opt_res['Demanda']['SEMANA']))

        # Parámetros
        Demanda = crear_diccionario(inputs_opt_res['Demanda'], ['SEMANA'], 'DEMANDA')
        Oferta_Int = crear_diccionario(inputs_opt_res['Oferta'], ['ZONA','SEMANA'], 'OFERTA')
        Oferta_Com = crear_diccionario(inputs_opt_res['Compras'], ['ZONA','SEMANA'], 'DISPONIBLE')
        
        Precio_Sac = crear_diccionario(inputs_opt_res['CV_PDN'], ['PLANTA'], 'CV_PDN')
        Retoma_Sac = crear_diccionario(inputs_opt_res['CV_PDN'], ['PLANTA'], 'RETOMAS')
        Costo_Sac = {k: Precio_Sac[k] - Retoma_Sac[k] for k in Precio_Sac}
        
        Costo_Viaje_Int = crear_diccionario(inputs_opt_res['CTransporteZF'], ['ZONA','PLANTA'], 'C_TRANS_ZF')
        Costo_Viaje_Comp = crear_diccionario(inputs_opt_res['CTransporteZFC'], ['ZONA','PLANTA'], 'C_TRANS_ZF')
        Costo_Tans_PT = crear_diccionario(inputs_opt_res['CTransporteE'], ['PLANTA'], 'C_TRANS_E')
        Capacidad = crear_diccionario(inputs_opt_res['Cap_Planta'], ['PLANTA'], 'CAP_PLANTA')
        Precio_Int = crear_diccionario(inputs_opt_res['CR_INTEGRADA'], ['ZONA'], 'CR_INTEGRADA')
        Precio_Comp = crear_diccionario(inputs_opt_res['CR_COMPRADA'], ['ZONA'], 'CR_COMPRADA')
        
        rdto = crear_diccionario_rdto_total(inputs_opt_res)
        Precio_Kg = crear_diccionario(inputs_opt_res['PRECIOKG'], ['ZONA'], 'PRECIO')
        Peso_Res = crear_diccionario(inputs_opt_res['PESORES'], ['ZONA'], 'PESO')
        costo_f = crear_diccionario(inputs_opt_res['COSTO_F'], ['PLANTA'], 'COSTO_F_SEM')

        # Modelo
        modelo = LpProblem("CostoSacrificio", LpMaximize)

        # Variables
        res_int = LpVariable.dicts('res_int', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        res_comp = LpVariable.dicts('res_comp', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_int = LpVariable.dicts('viaje_Int_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_com = LpVariable.dicts('viaje_Com_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_envigado = LpVariable.dicts('viaje_envigado', [(p,t) for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        compra_res = LpVariable.dicts('compra_res',[(z,p,t) for z in Zona for p in Planta_S for t in Semana],cat='Binary')

        # Función Objetivo
        modelo += lpSum(
            (res_int[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg +
            res_comp[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg -
            res_int[z,p,t] * Precio_Int.get((z),0) -
            res_comp[z,p,t] * Precio_Comp.get((z),0) -
            res_int[z,p,t] * Costo_Sac.get((p),0) -
            res_comp[z,p,t] * Costo_Sac.get((p),0) -
            viaje_int[z,p,t] * Costo_Viaje_Int.get((z,p),0) -
            viaje_com[z,p,t] * Costo_Viaje_Comp.get((z,p),0) -
            viaje_envigado[p,t] * Costo_Tans_PT.get((p),0))
            for z in Zona for p in Planta_S for t in Semana
        )

        # Restricciones
        for t in Semana:
            modelo += (lpSum(res_int[z,p,t] for z in Zona for p in Planta_S) + 
                       lpSum(res_comp[z,p,t] for z in Zona for p in Planta_S)) == Demanda[t]

        for z in Zona:
            for t in Semana:
                modelo += lpSum(res_int[z,p,t] for p in Planta_S) <= Oferta_Int.get((z,t),0)
                modelo += lpSum(res_comp[z,p,t] for p in Planta_S) <= Oferta_Com.get((z,t),0)

        for p in Planta_S:
            for t in Semana:
                modelo += (lpSum(res_int[z,p,t] for z in Zona) + lpSum(res_comp[z,p,t] for z in Zona) <= Capacidad.get((p),0))

        for z in Zona:
            for p in Planta_S:
                for t in Semana:
                    modelo += res_int[z,p,t] <= viaje_int[z,p,t] * 14
                    modelo += res_comp[z,p,t] <= viaje_com[z,p,t] * 14
                    modelo += res_comp[z,p,t] >= MinCompra * compra_res[z,p,t]
                    modelo += res_comp[z,p,t] <= 1000 * compra_res[z,p,t]

        for p in Planta_S:
            for t in Semana:
                modelo += (lpSum(res_int[z,p,t] for z in Zona) + lpSum(res_comp[z,p,t] for z in Zona)) <= viaje_envigado[p,t] * 84

        # Resolver
        modelo.solve(PULP_CBC_CMD(timeLimit=180))
        
        # Guardar Contexto
        contexto = {
            'Zona': Zona, 'Planta_S': Planta_S, 'Semana': Semana,
            'variables': {'res_int': res_int, 'res_comp': res_comp, 'viaje_int': viaje_int, 'viaje_com': viaje_com, 'viaje_envigado': viaje_envigado},
            'parametros': {
                'Precio_Int': Precio_Int, 'Precio_Comp': Precio_Comp, 'Costo_Sac': Costo_Sac,
                'Costo_Viaje_Int': Costo_Viaje_Int, 'Costo_Viaje_Comp': Costo_Viaje_Comp, 'Costo_Tans_PT': Costo_Tans_PT,
                'Peso_Res': Peso_Res, 'rdto': rdto, 'valor_kg': valor_kg, 'Demanda': Demanda,
                'Oferta_Int': Oferta_Int, 'Oferta_Com': Oferta_Com, 'Capacidad': Capacidad,
                'Costo_F': costo_f # Agregamos costos fijos
            }
        }
        
        # Calcular Costos Variables
        val_costo_int = sum((res_int[z,p,t].varValue or 0) * Precio_Int.get((z),0) for z in Zona for p in Planta_S for t in Semana)
        val_costo_comp = sum((res_comp[z,p,t].varValue or 0) * Precio_Comp.get((z),0) for z in Zona for p in Planta_S for t in Semana)
        val_costo_sac = sum((res_int[z,p,t].varValue or 0 + res_comp[z,p,t].varValue or 0) * Costo_Sac.get((p),0) for z in Zona for p in Planta_S for t in Semana)
        val_costo_tte_res = sum((viaje_int[z,p,t].varValue or 0) * Costo_Viaje_Int.get((z,p),0) + (viaje_com[z,p,t].varValue or 0) * Costo_Viaje_Comp.get((z,p),0) for z in Zona for p in Planta_S for t in Semana)
        val_costo_tte_pt = sum((viaje_envigado[p,t].varValue or 0) * Costo_Tans_PT.get((p),0) for p in Planta_S for t in Semana)
        
        # Calcular Ingresos y Kgs
        val_carne = sum((res_int[z,p,t].varValue or 0 + res_comp[z,p,t].varValue or 0) * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg for z in Zona for p in Planta_S for t in Semana)
        total_kg_producidos = val_carne / valor_kg if valor_kg > 0 else 0

        # Calcular Costo Fijo (Solo plantas utilizadas * Numero Semanas)
        plantas_usadas = set()
        for z in Zona:
            for p in Planta_S:
                for t in Semana:
                    if (res_int[z,p,t].varValue or 0) > 0 or (res_comp[z,p,t].varValue or 0) > 0:
                        plantas_usadas.add(p)
        
        num_semanas = len(Semana)
        total_costo_fijo = sum(costo_f.get(p, 0) * num_semanas for p in plantas_usadas)

        # Valorización Aritmética
        total_costos_variables = val_costo_int + val_costo_comp + val_costo_sac + val_costo_tte_res + val_costo_tte_pt
        val_valorizacion = val_carne - total_costos_variables

        costos = {
            'Costo Integración': val_costo_int, 'Costo Compras': val_costo_comp, 'Costo Sacrificio': val_costo_sac,
            'Costo Transporte Reses': val_costo_tte_res, 'Costo Transporte Canales': val_costo_tte_pt,
            'Valor Carne': val_carne, 'Valorización Total': val_valorizacion
        }
        
        # Guardar KPIs extra para el comparativo
        contexto['KPIs'] = {
            'Total Kg': total_kg_producidos,
            'Total Costo Fijo': total_costo_fijo
        }
        
        return modelo, contexto, costos
        
    except Exception as e:
        st.error(f"Error al ejecutar el modelo: {str(e)}")
        return None, None, None

# -----------------------------------------------------------------------------
# 5. LÓGICA DE ESCENARIO HIPOTÉTICO (AGUACHICA)
# -----------------------------------------------------------------------------
def calcular_escenario_hipotetico_detallado(contexto, planta_objetivo="AGUACHICA"):
    acumuladores = {'Costo Integración': 0, 'Costo Compras': 0, 'Costo Sacrificio': 0, 'Costo Transporte Reses': 0, 'Costo Transporte Canales': 0, 'Valor Carne': 0}
    volumen_int = {}
    volumen_comp = {}
    total_reses = 0
    total_kg_producidos = 0
    
    # Params
    P = contexto['parametros']
    if planta_objetivo not in P['Costo_Sac']: return None

    # Procesar Integradas
    for (z, p, t), var in contexto['variables']['res_int'].items():
        if var.varValue and var.varValue > 0:
            qty = var.varValue
            total_reses += qty
            acumuladores['Costo Integración'] += qty * P['Precio_Int'].get(z, 0)
            acumuladores['Costo Sacrificio'] += qty * P['Costo_Sac'].get(planta_objetivo, 0)
            kg_carne = qty * P['Peso_Res'].get(z, 0) * P['rdto'].get((z, planta_objetivo), 0)
            acumuladores['Valor Carne'] += kg_carne * P['valor_kg']
            total_kg_producidos += kg_carne
            if (z, t) not in volumen_int: volumen_int[(z, t)] = 0
            volumen_int[(z, t)] += qty

    # Procesar Compradas
    for (z, p, t), var in contexto['variables']['res_comp'].items():
        if var.varValue and var.varValue > 0:
            qty = var.varValue
            total_reses += qty
            acumuladores['Costo Compras'] += qty * P['Precio_Comp'].get(z, 0)
            acumuladores['Costo Sacrificio'] += qty * P['Costo_Sac'].get(planta_objetivo, 0)
            kg_carne = qty * P['Peso_Res'].get(z, 0) * P['rdto'].get((z, planta_objetivo), 0)
            acumuladores['Valor Carne'] += kg_carne * P['valor_kg']
            total_kg_producidos += kg_carne
            if (z, t) not in volumen_comp: volumen_comp[(z, t)] = 0
            volumen_comp[(z, t)] += qty

    # Fletes (Agrupados)
    for (z, t), qty in volumen_int.items():
        acumuladores['Costo Transporte Reses'] += math.ceil(qty / 14) * P['Costo_Viaje_Int'].get((z, planta_objetivo), 0)
    for (z, t), qty in volumen_comp.items():
        acumuladores['Costo Transporte Reses'] += math.ceil(qty / 14) * P['Costo_Viaje_Comp'].get((z, planta_objetivo), 0)
    
    # Flete Canales
    acumuladores['Costo Transporte Canales'] = math.ceil(total_reses / 84) * P['Costo_Tans_PT'].get(planta_objetivo, 0)
    
    # Costo Fijo (Solo Aguachica * Semanas)
    num_semanas = len(contexto['Semana'])
    total_costo_fijo = P['Costo_F'].get(planta_objetivo, 0) * num_semanas

    # Totales
    costos_tot_var = sum([v for k, v in acumuladores.items() if 'Costo' in k])
    acumuladores['Valorización Total'] = acumuladores['Valor Carne'] - costos_tot_var
    
    # Agregar KPIs
    acumuladores['Total Kg'] = total_kg_producidos
    acumuladores['Total Costo Fijo'] = total_costo_fijo
    
    return acumuladores

# -----------------------------------------------------------------------------
# 6. INTERFAZ DE USUARIO
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Configuración")
    uploaded_file = st.file_uploader("Cargar Excel", type=['xlsx', 'xls'])
    valor_kg = st.number_input("Valor comercial Kg carne ($)", min_value=0.0, value=22000.0, step=1000.0)
    MinCompra = st.number_input("Cantidad mínima viable para compra de reses", min_value=0.0, value=14.0, step=1.0)
    if uploaded_file: st.success("Cargado")

if uploaded_file:
    inputs_opt_res = procesar_archivo(uploaded_file)
    if inputs_opt_res:
        st.subheader("Vista previa")
        sheet_names = list(inputs_opt_res.keys())
        selected_sheet = st.selectbox("Hoja:", sheet_names)
        Hoja_Editada = st.data_editor(inputs_opt_res[selected_sheet], key=f'editor_{selected_sheet}', num_rows='dynamic')
        
        if st.button("Guardar cambios en hoja"):
            inputs_opt_res[selected_sheet] = Hoja_Editada
            st.session_state['edited_data'] = inputs_opt_res
            st.success("Guardado.")

        current_data = st.session_state.get('edited_data', inputs_opt_res)

        if st.button("Ejecutar Modelo"):
            with st.spinner("Ejecutando..."):
                modelo, contexto, costos = ejecutar_modelo(current_data, valor_kg, MinCompra)
            if modelo:
                st.success("¡Optimización completada!")
                st.session_state.update({'modelo': modelo, 'contexto': contexto, 'costos': costos})

        if 'contexto' in st.session_state:
            contexto = st.session_state['contexto']
            costos = st.session_state['costos']

            # --- RESULTADOS: TABLA CONSOLIDADA ---
            st.subheader("Plan de Sacrificio Consolidado")
            data = []
            for (z, p, t) in [(z,p,t) for z in contexto['Zona'] for p in contexto['Planta_S'] for t in contexto['Semana']]:
                v_int = contexto['variables']['res_int'].get((z,p,t)).varValue or 0
                v_comp = contexto['variables']['res_comp'].get((z,p,t)).varValue or 0
                if v_int > 0 or v_comp > 0:
                    data.append({'Zona': z, 'Planta': p, 'Semana': t, 'Reses Int': v_int, 'Reses Comp': v_comp, 'Total': v_int+v_comp})
            
            if data:
                df_con = pd.DataFrame(data).sort_values(['Semana', 'Zona', 'Planta'])
                st.dataframe(df_con)
                # Descarga Excel
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_con.to_excel(writer, index=False)
                st.download_button("Descargar Excel", output.getvalue(), "plan_sacrificio.xlsx")
            else:
                st.warning("No hay datos en la solución.")

            # --- RESULTADOS: DESGLOSE COSTOS ---
            st.subheader("Desglose de Costos y Valores")
            st.dataframe(pd.DataFrame.from_dict(costos, orient='index', columns=['Valor ($)']).style.format("{:,.0f}"))

            # --- COMPARATIVO DE ESCENARIOS (CON SIGNOS NEGATIVOS) ---
            escenario_b = calcular_escenario_hipotetico_detallado(contexto, "AGUACHICA")
            if escenario_b:
                st.markdown("---")
                st.subheader("⚖️ Comparativo: Óptimo vs. Todo a Aguachica")
                data_unif = []
                
                # Datos Escenario Optimo
                kpis_opt = contexto['KPIs']
                total_costo_var_opt = costos['Valor Carne'] - costos['Valorización Total']
                total_costo_total_opt = total_costo_var_opt + kpis_opt['Total Costo Fijo']
                costo_final_kg_opt = total_costo_total_opt / kpis_opt['Total Kg'] if kpis_opt['Total Kg'] > 0 else 0
                
                # Datos Escenario Aguachica
                total_costo_var_agua = escenario_b['Valor Carne'] - escenario_b['Valorización Total']
                total_costo_total_agua = total_costo_var_agua + escenario_b['Total Costo Fijo']
                costo_final_kg_agua = total_costo_total_agua / escenario_b['Total Kg'] if escenario_b['Total Kg'] > 0 else 0

                # Filas normales
                for k in costos.keys():
                    val_opt = costos[k]
                    val_agua = escenario_b.get(k, 0)
                    diff = val_opt - val_agua
                    pct = (diff / val_agua) if val_agua != 0 else 0.0
                    
                    # Ajuste Visual: Costos en negativo
                    es_costo = 'Costo' in k
                    v_opt_vis = val_opt * -1 if es_costo else val_opt
                    v_agua_vis = val_agua * -1 if es_costo else val_agua
                    
                    data_unif.append({'Concepto': k, 'Escenario Óptimo': v_opt_vis, 'Escenario Aguachica': v_agua_vis, 'Diferencia ($)': diff, 'Var. (%)': pct})
                
                # Agregar Fila Costo Final por Kg
                diff_kg = costo_final_kg_opt - costo_final_kg_agua
                pct_kg = (diff_kg / costo_final_kg_agua) if costo_final_kg_agua != 0 else 0
                data_unif.append({
                    'Concepto': 'Costo Final por Kg (Inc. Fijo)', 
                    'Escenario Óptimo': costo_final_kg_opt * -1, 
                    'Escenario Aguachica': costo_final_kg_agua * -1, 
                    'Diferencia ($)': diff_kg, 
                    'Var. (%)': pct_kg
                })

                df_comp = pd.DataFrame(data_unif)
                def estilo_comp(s):
                    styler = s.format({'Escenario Óptimo': '${:,.0f}', 'Escenario Aguachica': '${:,.0f}', 'Diferencia ($)': '${:,.0f}', 'Var. (%)': '{:.2%}'})
                    styler.apply(lambda x: [f'color: {"#2ca02c" if (("Valor" in x["Concepto"] and x["Var. (%)"]>0) or ("Costo" in x["Concepto"] and x["Var. (%)"]<0)) else "#d62728"}; font-weight: bold' if col == 'Var. (%)' else '' for col in x.index], axis=1)
                    styler.apply(lambda x: ['background-color: #f0f0f0; font-weight: bold' if x['Concepto'] == 'Valorización Total' else '' for _ in x], axis=1)
                    styler.apply(lambda x: ['background-color: #e6f3ff; font-weight: bold; border-top: 2px solid #000' if 'Costo Final' in x['Concepto'] else '' for _ in x], axis=1)
                    return styler
                
                st.dataframe(estilo_comp(df_comp.style), use_container_width=True)
                mejora = costos['Valorización Total'] - escenario_b['Valorización Total']
                st.info(f"💡 Beneficio por optimizar: **${mejora:,.0f}** vs Aguachica.")

            # --- ANÁLISIS POR ZONA ---
            st.markdown("---")
            st.subheader("📊 Análisis Detallado por Zona")
            tab1, tab2 = st.tabs(["Análisis por Zona", "Análisis Transporte"])
            
            with tab1:
                col1, col2 = st.columns([1, 3])
                with col1:
                    zona_sel = st.selectbox("Zona:", contexto['Zona'], key="z_sel") if 'Zona' in contexto else None
                    vista = st.radio("Vista:", ["Consolidado", "Por Planta"])
                
                with col2:
                    # Calcular datos zona
                    z_data = []
                    for t in contexto['Semana']:
                        for p in contexto['Planta_S']:
                            ri = contexto['variables']['res_int'].get((zona_sel, p, t)).varValue or 0
                            rc = contexto['variables']['res_comp'].get((zona_sel, p, t)).varValue or 0
                            if ri > 0 or rc > 0:
                                params = contexto['parametros']
                                c_int = ri * params['Precio_Int'].get(zona_sel, 0)
                                c_sac = (ri + rc) * params['Costo_Sac'].get(p, 0)
                                ing = (ri + rc) * params['Peso_Res'].get(zona_sel, 0) * params['rdto'].get((zona_sel, p), 0) * params['valor_kg']
                                z_data.append({'Semana': str(t), 'Planta': p, 'Reses Int': ri, 'Reses Comp': rc, 
                                               'Costo Int ($)': c_int, 'Costo Comp ($)': rc * params['Precio_Comp'].get(zona_sel, 0),
                                               'Costo Sac Int ($)': ri * params['Costo_Sac'].get(p, 0), 'Costo Sac Comp ($)': rc * params['Costo_Sac'].get(p, 0),
                                               'Ingreso Int ($)': ri*params['Peso_Res'].get(zona_sel)*params['rdto'].get((zona_sel,p))*params['valor_kg'], 
                                               'Ingreso Comp ($)': rc*params['Peso_Res'].get(zona_sel)*params['rdto'].get((zona_sel,p))*params['valor_kg']})
                    
                    if z_data:
                        df_z = pd.DataFrame(z_data)
                        
                        # MÉTRICAS PERSONALIZADAS
                        st.subheader(f"Resumen - {zona_sel}")
                        def metrica_html(label, value):
                            st.markdown(f"<div style='background-color:#f8f9fa;padding:10px;border-radius:5px;border:1px solid #ddd'><p style='margin:0;font-size:14px;color:#666'>{label}</p><p style='margin:0;font-size:18px;font-weight:bold;color:#333'>{value}</p></div>", unsafe_allow_html=True)
                        
                        c_a, c_b, c_c = st.columns(3)
                        with c_a: metrica_html("Reses Integradas", f"{df_z['Reses Int'].sum():,.0f}")
                        with c_b: metrica_html("Reses Compradas", f"{df_z['Reses Comp'].sum():,.0f}")
                        with c_c: metrica_html("Costo Total Reses", f"${df_z['Costo Int ($)'].sum() + df_z['Costo Comp ($)'].sum():,.0f}")
                        st.write("")

                        # TABLAS SEMANAS EN FILAS
                        def tabla_sem_filas(df_in, tipo="Unidades"):
                            df = df_in.copy()
                            if tipo == "Unidades":
                                view = df.groupby('Semana')[['Reses Int', 'Reses Comp']].sum()
                                view['Total'] = view.sum(axis=1)
                                view.loc['TOTAL'] = view.sum()
                                return view.style.format("{:,.0f}")
                            else:
                                view = df.groupby('Semana')[['Costo Int ($)', 'Costo Comp ($)', 'Costo Sac Int ($)', 'Costo Sac Comp ($)', 'Ingreso Int ($)', 'Ingreso Comp ($)']].sum()
                                view['Subtotal Reses'] = view['Costo Int ($)'] + view['Costo Comp ($)']
                                view['Subtotal Sac'] = view['Costo Sac Int ($)'] + view['Costo Sac Comp ($)']
                                view['Subtotal Ing'] = view['Ingreso Int ($)'] + view['Ingreso Comp ($)']
                                cols = ['Costo Int ($)', 'Costo Comp ($)', 'Subtotal Reses', 'Costo Sac Int ($)', 'Costo Sac Comp ($)', 'Subtotal Sac', 'Ingreso Int ($)', 'Ingreso Comp ($)', 'Subtotal Ing']
                                view = view[cols]
                                view.loc['TOTAL'] = view.sum()
                                
                                s = view.style.format("${:,.0f}")
                                s.applymap(lambda x: 'color: #d62728', subset=[c for c in cols if 'Costo' in c and 'Sub' not in c])
                                s.applymap(lambda x: 'color: #2ca02c', subset=[c for c in cols if 'Ingreso' in c and 'Sub' not in c])
                                s.applymap(lambda x: 'font-weight: bold; background-color: #f0f0f0; color: black', subset=[c for c in cols if 'Sub' in c])
                                s.apply(lambda x: ['font-weight: bold; border-top: 2px solid black']*len(x) if x.name=='TOTAL' else ['']*len(x), axis=1)
                                return s

                        df_cons = df_z if vista == "Consolidado" else df_z[df_z['Planta'] == st.selectbox("Planta:", df_z['Planta'].unique())]
                        
                        st.subheader("Unidades")
                        st.dataframe(tabla_sem_filas(df_cons, "Unidades"), use_container_width=True)
                        st.subheader("Financiera")
                        st.dataframe(tabla_sem_filas(df_cons, "Financiera"), use_container_width=True)
                    else:
                        st.info("Sin datos.")

            with tab2: # Transporte
                st.subheader("Análisis Transporte")
                z_tte = st.selectbox("Zona Tte:", contexto['Zona'], key="zt")
                tte_data = []
                for t in contexto['Semana']:
                    for p in contexto['Planta_S']:
                        vi = contexto['variables']['viaje_int'].get((z_tte, p, t)).varValue or 0
                        vc = contexto['variables']['viaje_com'].get((z_tte, p, t)).varValue or 0
                        if vi+vc > 0:
                            ci = contexto['parametros']['Costo_Viaje_Int'].get((z_tte, p), 0)
                            cc = contexto['parametros']['Costo_Viaje_Comp'].get((z_tte, p), 0)
                            tte_data.append({'Semana': str(t), 'Planta': p, 'Viajes Int': vi, 'Viajes Comp': vc, 
                                             'Costo Unit Int': ci, 'Costo Unit Comp': cc, 'Total Int': vi*ci, 'Total Comp': vc*cc})
                if tte_data:
                    df_tte = pd.DataFrame(tte_data)
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Viajes Int", f"{df_tte['Viajes Int'].sum():,.0f}")
                    c2.metric("Viajes Comp", f"{df_tte['Viajes Comp'].sum():,.0f}")
                    c3.metric("Costo Int", f"${df_tte['Total Int'].sum():,.0f}")
                    c4.metric("Costo Comp", f"${df_tte['Total Comp'].sum():,.0f}")
                    
                    st.dataframe(df_tte.style.format({'Semana': '{:.2f}', 'Costo Unit Int': '${:,.0f}', 'Costo Unit Comp': '${:,.0f}', 'Total Int': '${:,.0f}', 'Total Comp': '${:,.0f}', 'Viajes Int': '{:,.0f}', 'Viajes Comp': '{:,.0f}'}), use_container_width=True)
                else:
                    st.info("Sin transporte.")
else:
    st.info("Carga el archivo.")

    # Plantilla de Excel (opcional)
    with st.expander("Descargar plantilla de Excel"):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            Zonas = ['ANTIOQUIA','VALLEDUPAR','COSTA']
            Semanas = ['27.2025', '28.2025']
            Plantas = ['AGUACHICA','FRIGOSINU','COROZAL']
            
            # Hojas básicas
            pd.DataFrame({'ZONA': Zonas*len(Semanas), 'SEMANA': Semanas*len(Zonas), 'OFERTA': 25}).to_excel(writer, 'Oferta', index=False)
            pd.DataFrame({'SEMANA': Semanas, 'DEMANDA': 100}).to_excel(writer, 'Demanda', index=False)
            pd.DataFrame({'PLANTA': Plantas, 'CV_PDN': 130000, 'RETOMAS': 1000}).to_excel(writer, 'CV_PDN', index=False)
            pd.DataFrame({'PLANTA': Plantas, 'COSTO_F_SEM': 50000000}).to_excel(writer, 'COSTO_F', index=False) # Nueva Hoja Costo Fijo
            pd.DataFrame({'ZONA': Zonas*len(Plantas), 'PLANTA': Plantas*len(Zonas), 'C_TRANS_ZF': 1200000}).to_excel(writer, 'CTransporteZF', index=False)
            pd.DataFrame({'ZONA': Zonas*len(Plantas), 'PLANTA': Plantas*len(Zonas), 'MERMA': 0.02}).to_excel(writer, 'MermaTTE', index=False)
            pd.DataFrame({'PLANTA': Plantas, 'M_CANAL_CALIENTE': 0.01, 'M_CANAL_FRIO': 0.01}).to_excel(writer, 'MermasPlantas', index=False)
            pd.DataFrame({'ZONA': Zonas*len(Plantas), 'PLANTA': Plantas*len(Zonas), 'C_TRANS_ZF': 1200000}).to_excel(writer, 'CTransporteZFC', index=False)
            pd.DataFrame({'PLANTA': Plantas, 'C_TRANS_E': 4000000}).to_excel(writer, 'CTransporteE', index=False)
            pd.DataFrame({'PLANTA': Plantas, 'CAP_PLANTA': 50}).to_excel(writer, 'Cap_Planta', index=False)
            pd.DataFrame({'ZONA': Zonas*len(Semanas), 'SEMANA': Semanas*len(Zonas), 'DISPONIBLE': 25}).to_excel(writer, 'Compras', index=False)
            pd.DataFrame({'ZONA': Zonas, 'PESO': 400}).to_excel(writer, 'PESORES', index=False)
            pd.DataFrame({'ZONA': Zonas, 'PRECIO': 8000}).to_excel(writer, 'PRECIOKG', index=False)
            pd.DataFrame({'ZONA': Zonas, 'PRECIO': 2500000}).to_excel(writer, 'CR_COMPRADA', index=False)
            pd.DataFrame({'ZONA': Zonas, 'PRECIO': 1500000}).to_excel(writer, 'CR_INTEGRADA', index=False)

        st.download_button("Descargar plantilla", output.getvalue(), "plantilla.xlsx")
