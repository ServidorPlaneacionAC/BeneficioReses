import streamlit as st
import pandas as pd
from pulp import *
from io import BytesIO
import time
import matplotlib

# Configuración de la página
st.set_page_config(page_title="Modelo de Sacrificio de Reses", layout="wide")
st.title("Optimización de Sacrificio de Reses")

def aplicar_estilos_financiera(df):
    """
    Aplica estilos condicionales a la tabla financiera.
    CORRECCIÓN: Devuelve un DataFrame de estilos compatible con axis=None.
    """
    # 1. Crear un DataFrame de estilos vacío con la misma estructura que df
    styles = pd.DataFrame('', index=df.index, columns=df.columns)
    
    # Si no existe la columna Concepto o el df está vacío, retornamos estilos vacíos
    if 'Concepto' not in df.columns or df.empty:
        return styles

    # 2. Iterar sobre las filas para aplicar lógica
    for idx, row in df.iterrows():
        # Convertimos a string para evitar errores si hay valores nulos
        concepto = str(row['Concepto'])
        estilo_fila = ''
        
        # Determinar el estilo base según el texto en 'Concepto'
        if 'SUBTOTAL' in concepto:
            estilo_fila = 'font-weight: bold; background-color: #f0f0f0; color: black'
        elif 'Costo' in concepto and 'Ingreso' not in concepto:
            estilo_fila = 'color: #d62728'  # Rojo
        elif 'Ingreso' in concepto:
            estilo_fila = 'color: #2ca02c'  # Verde
            
        # Aplicar el estilo a toda la fila
        if estilo_fila:
            styles.loc[idx, :] = estilo_fila
            
        # Refinar: añadir negrita extra solo a la celda del título 'Concepto' si es Costo o Ingreso
        if ('Costo' in concepto or 'Ingreso' in concepto) and 'SUBTOTAL' not in concepto:
            styles.loc[idx, 'Concepto'] = f"{estilo_fila}; font-weight: bold"

    return styles

def mostrar_dataframe_con_estilos(df, height=400):
    """Muestra un DataFrame con estilos aplicados y maneja errores."""
    try:
        # Aplicamos la función de estilos
        st.dataframe(
            df.style.apply(aplicar_estilos_financiera, axis=None),
            use_container_width=True, 
            height=height
        )
    except Exception as e:
        # Si falla el estilo, mostramos la tabla normal y el error como advertencia
        st.warning(f"No se pudieron aplicar los colores: {e}")
        st.dataframe(df, use_container_width=True, height=height)

# --- FIN BLOQUE DE ESTILOS ---

# Función para cargar y procesar el archivo Excel
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

# Función para crear diccionarios de parámetros
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

# Función principal del modelo
def ejecutar_modelo(inputs_opt_res, valor_kg):
    try:
        # Definición de conjuntos
        Zona = list(set(inputs_opt_res['Oferta']['ZONA']))
        Planta_S = list(set(inputs_opt_res['CV_PDN']['PLANTA']))
        Semana = list(set(inputs_opt_res['Demanda']['SEMANA']))

        # Definición de parámetros
        Demanda = crear_diccionario(inputs_opt_res['Demanda'], ['SEMANA'], 'DEMANDA')
        Oferta_Int = crear_diccionario(inputs_opt_res['Oferta'], ['ZONA','SEMANA'], 'OFERTA')
        Oferta_Com = crear_diccionario(inputs_opt_res['Compras'], ['ZONA','SEMANA'], 'DISPONIBLE')
        Costo_Sac = crear_diccionario(inputs_opt_res['CV_PDN'], ['PLANTA'], 'CV_PDN')
        Costo_Viaje_Int = crear_diccionario(inputs_opt_res['CTransporteZF'], ['ZONA','PLANTA'], 'C_TRANS_ZF')
        Costo_Viaje_Comp = crear_diccionario(inputs_opt_res['CTransporteZFC'], ['ZONA','PLANTA'], 'C_TRANS_ZF')
        Costo_Tans_PT = crear_diccionario(inputs_opt_res['CTransporteE'], ['PLANTA'], 'C_TRANS_E')
        Capacidad = crear_diccionario(inputs_opt_res['Cap_Planta'], ['PLANTA'], 'CAP_PLANTA')
        Precio_Int = crear_diccionario(inputs_opt_res['CR_INTEGRADA'], ['ZONA'], 'CR_INTEGRADA')
        Precio_Comp = crear_diccionario(inputs_opt_res['CR_COMPRADA'], ['ZONA'], 'CR_COMPRADA')
        rdto = crear_diccionario(inputs_opt_res['RENDIMIENTO'], ['ZONA','PLANTA'], 'RDTO')
        Precio_Kg = crear_diccionario(inputs_opt_res['PRECIOKG'], ['ZONA'], 'PRECIO')
        Peso_Res = crear_diccionario(inputs_opt_res['PESORES'], ['ZONA'], 'PESO')

        # Creación del modelo
        modelo = LpProblem("CostoSacrificio", LpMaximize)

        # Variables de decisión
        res_int = LpVariable.dicts('res_int', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        res_comp = LpVariable.dicts('res_comp', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_int = LpVariable.dicts('viaje_Int_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_com = LpVariable.dicts('viaje_Com_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_envigado = LpVariable.dicts('viaje_envigado', [(p,t) for p in Planta_S for t in Semana], lowBound=0, cat='Integer')

        # Función objetivo
        modelo += lpSum(
            (res_int[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg +
            res_comp[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg -
            res_int[z,p,t] * Precio_Int.get((z),0) -
            res_comp[z,p,t] * Precio_Comp.get((z),0) -
            res_int[z,p,t] * Costo_Sac.get((p),0) -
            res_comp[z,p,t] * Costo_Sac.get((p),0) -
            viaje_int[z,p,t] * Costo_Viaje_Int.get((z,p),0) -
            viaje_com[z,p,t] * Costo_Viaje_Comp.get((z,p),0) -
            viaje_envigado[p,t] * Costo_Tans_PT.get((p),0)
            for z in Zona for p in Planta_S for t in Semana)
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

        for p in Planta_S:
            for t in Semana:
                modelo += (lpSum(res_int[z,p,t] for z in Zona) + lpSum(res_comp[z,p,t] for z in Zona)) <= viaje_envigado[p,t] * 84

        # Resolver el modelo
        modelo.solve(PULP_CBC_CMD(timeLimit=60))
        
        # Preparar resultados
        contexto = {
            'Zona': Zona,
            'Planta_S': Planta_S,
            'Semana': Semana,
            'variables': {
                'res_int': res_int,
                'res_comp': res_comp,
                'viaje_int': viaje_int,
                'viaje_com': viaje_com,
                'viaje_envigado': viaje_envigado
            },
            'parametros': {
                'Precio_Int': Precio_Int,
                'Precio_Comp': Precio_Comp,
                'Costo_Sac': Costo_Sac,
                'Peso_Res': Peso_Res,
                'rdto': rdto,
                'valor_kg': valor_kg,
                'Demanda': Demanda,
                'Oferta_Int': Oferta_Int,
                'Oferta_Com': Oferta_Com,
                'Capacidad': Capacidad
            }
        }
        
        # Calcular métricas de costos
        costos = {
            'Costo Integración': sum(res_int[z,p,t].varValue * Precio_Int.get((z),0) 
                                for z in Zona for p in Planta_S for t in Semana),
            'Costo Compras': sum(res_comp[z,p,t].varValue * Precio_Comp.get((z),0) 
                            for z in Zona for p in Planta_S for t in Semana),
            'Costo Sacrificio': sum(res_int[z,p,t].varValue * Costo_Sac.get((p),0) 
                               for z in Zona for p in Planta_S for t in Semana) +
                              sum(res_comp[z,p,t].varValue * Costo_Sac.get((p),0) 
                               for z in Zona for p in Planta_S for t in Semana),
            'Costo Transporte Reses': sum(viaje_int[z,p,t].varValue * Costo_Viaje_Int.get((z,p),0) 
                                     for z in Zona for p in Planta_S for t in Semana) +
                                     sum(viaje_com[z,p,t].varValue * Costo_Viaje_Comp.get((z,p),0) 
                                     for z in Zona for p in Planta_S for t in Semana),
            'Costo Transporte Canales': sum(viaje_envigado[p,t].varValue * Costo_Tans_PT.get((p),0) 
                                       for p in Planta_S for t in Semana),
            'Valor Carne': sum(res_int[z,p,t].varValue * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg 
                             for z in Zona for p in Planta_S for t in Semana) +
                          sum(res_comp[z,p,t].varValue * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg 
                             for z in Zona for p in Planta_S for t in Semana),
            'Valorización Total': value(modelo.objective)
        }
        
        return modelo, contexto, costos
        
    except Exception as e:
        st.error(f"Error al ejecutar el modelo: {str(e)}")
        return None, None, None

# Interfaz de usuario
with st.sidebar:
    st.header("Configuración del Modelo")
    uploaded_file = st.file_uploader("Cargar archivo Excel con parámetros", type=['xlsx', 'xls'])
    valor_kg = st.number_input("Valor comercial de Kg de carne ($)", min_value=0.0, value=22000.0, step=1000.0)
        
    if uploaded_file is not None:
        st.success("Archivo cargado correctamente")

if uploaded_file is not None:
    # Procesar archivo
    inputs_opt_res = procesar_archivo(uploaded_file)
    
    if inputs_opt_res is not None:
        # Mostrar vista previa de los datos
        st.subheader("Vista previa de los datos cargados")
        
        sheet_names = list(inputs_opt_res.keys())
        selected_sheet = st.selectbox("Seleccionar hoja para visualizar", sheet_names)

        #Mostrar un dataframe editable:
        Hoja_Editada = st.data_editor(
            inputs_opt_res[selected_sheet],
            key= f'editor_{selected_sheet}',
            num_rows='dynamic')

        # Botón para guardar cambios

        if st.button("Guardar cambios en esta hoja"):
            inputs_opt_res[selected_sheet] = Hoja_Editada
            st.session_state['edited_data'] = inputs_opt_res  # Guardar en session_state
            st.success("Cambios guardados! Puede ejecutar el modelo con los datos actualizados.")
        
        # Ejecutar modelo con los datos actuales (ya sean originales o editados)
        current_data = st.session_state.get('edited_data', inputs_opt_res)
        
        if st.button("Ejecutar Modelo de Optimización"):
            with st.spinner("Ejecutando modelo, por favor espere..."):
                start_time = time.time()
                modelo, contexto, costos = ejecutar_modelo(current_data, valor_kg)
                execution_time = time.time() - start_time
            
            if modelo is not None and costos is not None:
                st.success("Modelo ejecutado exitosamente!")
                st.write(f"Tiempo de ejecución: {execution_time:.2f} segundos")

                # Guardar resultados en session_state
                st.session_state['modelo'] = modelo
                st.session_state['contexto'] = contexto
                st.session_state['costos'] = costos

            # Mostrar resultados SI existen en session_state (aunque no se acabe de ejecutar)
        if 'contexto' in st.session_state:
            contexto = st.session_state['contexto']
            costos = st.session_state['costos']
            
            # Resultados principales
            #st.subheader("Resultados Generales")
            #estado_modelo = LpStatus[st.session_state['modelo'].status]
            
            # col1, col2 = st.columns(2)
            # col1.metric("Estado del modelo", estado_modelo)
            # col2.metric("Valorización total ($)", f"{costos['Valorización Total']:,.0f}")
            
            # Crear DataFrame consolidado
            st.subheader("Plan de Sacrificio Consolidado")
            
            # Preparar datos para todas las variables
            data = []
            semanas = contexto['Semana']
            plantas = contexto['Planta_S']
            zonas = contexto['Zona']
            
            # Crear combinaciones únicas de (zona, planta, semana)
            combinaciones = [(z, p, t) for z in zonas for p in plantas for t in semanas]
            
            for z, p, t in combinaciones:
                res_int_val = contexto['variables']['res_int'][(z, p, t)].varValue if (z, p, t) in contexto['variables']['res_int'] else 0
                res_comp_val = contexto['variables']['res_comp'][(z, p, t)].varValue if (z, p, t) in contexto['variables']['res_comp'] else 0
                
                # Solo agregar filas con valores positivos
                if res_int_val > 0 or res_comp_val > 0:
                    data.append({
                        'Zona': z,
                        'Planta': p,
                        'Semana': t,
                        'Reses integradas': res_int_val,
                        'Reses compradas': res_comp_val,
                        'Total reses': res_int_val + res_comp_val
                    })
            
            # Crear DataFrame
            if data:
                df_consolidado = pd.DataFrame(data)
                
                # Ordenar por semana, zona y planta
                df_consolidado = df_consolidado.sort_values(['Semana', 'Zona', 'Planta'])
                
                # Mostrar tabla
                st.dataframe(df_consolidado)
                
                # Opción para descargar
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_consolidado.to_excel(writer, sheet_name='Plan_Sacrificio', index=False)
                
                st.download_button(
                    label="Descargar plan completo en Excel",
                    data=output.getvalue(),
                    file_name="plan_sacrificio_consolidado.xlsx",
                    mime="application/vnd.ms-excel"
                )
            else:
                st.warning("No hay datos positivos para mostrar en la solución óptima")
            
            # Mostrar desglose de costos (se mantiene igual)
            st.subheader("Desglose de Costos y Valores")
            df_costos = pd.DataFrame.from_dict(costos, orient='index', columns=['Valor ($)'])
            st.dataframe(df_costos.style.format("{:,.0f}"))
            # ------------------------------------------------------------
            # COMPONENTE DE ANÁLISIS POR ZONA (NUEVO) - VERSIÓN CORREGIDA
            # ------------------------------------------------------------
            st.markdown("---")
            st.subheader("📊 Análisis Detallado por Zona")
            
            # Función auxiliar para obtener el valor de una variable PuLP
            def obtener_valor_pulp(variable):
                """Obtiene el valor de una variable PuLP, manejando diferentes tipos."""
                if variable is None:
                    return 0
                elif hasattr(variable, 'varValue'):
                    return variable.varValue if variable.varValue is not None else 0
                elif isinstance(variable, (int, float)):
                    return variable
                else:
                    return 0
    
            if 'contexto' in st.session_state:
                zonas_disponibles = contexto['Zona']
                
                # Crear pestañas para diferentes análisis
                tab1, tab2 = st.tabs(["📈 Análisis por Zona", "🚚 Análisis de Transporte"])
                
                with tab1:
                    col1, col2 = st.columns([1, 3])
                    
                    with col1:
                        zona_seleccionada = st.selectbox(
                            "Seleccionar Zona para análisis:",
                            options=zonas_disponibles,
                            key="zona_selector"
                        )
                        
                        # Opción para ver datos por planta o consolidado
                        vista_tipo = st.radio(
                            "Tipo de vista:",
                            ["Consolidado", "Por Planta"],
                            key=f"vista_{zona_seleccionada}"
                        )
                    
                    with col2:
                        # Calcular resumen para la zona seleccionada
                        zona_data = []
                        semanas = contexto['Semana']
                        plantas = contexto['Planta_S']
                        
                        for t in semanas:
                            for p in plantas:
                                # Obtener valores con manejo seguro
                                res_int_var = contexto['variables']['res_int'].get((zona_seleccionada, p, t))
                                res_comp_var = contexto['variables']['res_comp'].get((zona_seleccionada, p, t))
                                
                                res_int_val = obtener_valor_pulp(res_int_var)
                                res_comp_val = obtener_valor_pulp(res_comp_var)
                                
                                if res_int_val > 0 or res_comp_val > 0:
                                    # Obtener valores unitarios
                                    precio_int = contexto['parametros']['Precio_Int'].get(zona_seleccionada, 0)
                                    precio_comp = contexto['parametros']['Precio_Comp'].get(zona_seleccionada, 0)
                                    costo_sac = contexto['parametros']['Costo_Sac'].get(p, 0)
                                    peso_res = contexto['parametros']['Peso_Res'].get(zona_seleccionada, 0)
                                    rendimiento = contexto['parametros']['rdto'].get((zona_seleccionada, p), 0)
                                    valor_kg = contexto['parametros']['valor_kg']
                                    
                                    # Calcular costos
                                    costo_int_total = res_int_val * precio_int
                                    costo_comp_total = res_comp_val * precio_comp
                                    costo_sac_int = res_int_val * costo_sac
                                    costo_sac_comp = res_comp_val * costo_sac
                                    
                                    # Calcular ingresos
                                    ingreso_int = res_int_val * peso_res * rendimiento * valor_kg
                                    ingreso_comp = res_comp_val * peso_res * rendimiento * valor_kg
                                    
                                    zona_data.append({
                                        'Semana': t,
                                        'Planta': p,
                                        'Reses Int': int(res_int_val),
                                        'Reses Comp': int(res_comp_val),
                                        'Costo Int ($)': round(costo_int_total, 2),
                                        'Costo Comp ($)': round(costo_comp_total, 2),
                                        'Costo Sac Int ($)': round(costo_sac_int, 2),
                                        'Costo Sac Comp ($)': round(costo_sac_comp, 2),
                                        'Ingreso Int ($)': round(ingreso_int, 2),
                                        'Ingreso Comp ($)': round(ingreso_comp, 2)
                                    })                     
                        if zona_data:
                            df_zona = pd.DataFrame(zona_data)
                            
                            # Mostrar métricas resumidas
                            st.subheader(f"Resumen - {zona_seleccionada}")
                            col_a, col_b, col_c, col_d = st.columns(4)
                            
                            with col_a:
                                total_integradas = df_zona['Reses Int'].sum()
                                st.metric(
                                    label="Reses Integradas",
                                    value=f"{total_integradas:,.0f}"
                                )
                            
                            with col_b:
                                total_compradas = df_zona['Reses Comp'].sum()
                                st.metric(
                                    label="Reses Compradas",
                                    value=f"{total_compradas:,.0f}"
                                )
                            
                            with col_c:
                                total_costo_reses = df_zona['Costo Int ($)'].sum() + df_zona['Costo Comp ($)'].sum()
                                st.metric(
                                    label="Costo Total Reses",
                                    value=f"${total_costo_reses:,.0f}"
                                )
                            
                            with col_d:
                                total_ingreso = df_zona['Ingreso Int ($)'].sum() + df_zona['Ingreso Comp ($)'].sum()
                                st.metric(
                                    label="Ingreso Total",
                                    value=f"${total_ingreso:,.0f}"
                                )
                            
                            # 1. Definición de nombres y formatos
                            # 1. Definición de nombres y formatos
                            nombres_descriptivos = {
                                'Reses Int': 'Reses Integradas',
                                'Reses Comp': 'Reses Compradas',
                                'Total Reses': 'Total Reses',
                                
                                'Costo Int ($)': 'Costo Reses Integradas',
                                'Costo Comp ($)': 'Costo Reses Compradas',
                                'Subtotal Reses': 'SUBTOTAL: Costos de Reses',
                                
                                'Costo Sac Int ($)': 'Costo Sacrificio Int.',
                                'Costo Sac Comp ($)': 'Costo Sacrificio Comp.',
                                'Subtotal Sac': 'SUBTOTAL: Costos de Sacrificio',
                                
                                'Ingreso Int ($)': 'Ingreso Carne Int.',
                                'Ingreso Comp ($)': 'Ingreso Carne Comp.',
                                'Subtotal Ing': 'SUBTOTAL: Ingresos por Carne'
                            }
    
                            def generar_tabla_semanas_filas(df_source, tipo_tabla="Unidades"):
                                """Genera tabla con Semanas en filas y variables en columnas."""
                                df = df_source.copy()
                                df['Semana'] = df['Semana'].astype(str)
                                
                                if tipo_tabla == "Unidades":
                                    cols = ['Semana', 'Reses Int', 'Reses Comp']
                                    df_view = df[cols].copy()
                                    df_view['Total Reses'] = df_view['Reses Int'] + df_view['Reses Comp']
                                    
                                    total_row = {'Semana': 'TOTAL'}
                                    for col in ['Reses Int', 'Reses Comp', 'Total Reses']:
                                        total_row[col] = df_view[col].sum()
                                        
                                    df_view = pd.concat([df_view, pd.DataFrame([total_row])], ignore_index=True)
                                    df_view = df_view.rename(columns=nombres_descriptivos)
                                    df_view = df_view.set_index('Semana')
                                    return df_view.style.format("{:,.0f}")
                            
                                elif tipo_tabla == "Financiera":
                                    df['Subtotal Reses'] = df['Costo Int ($)'] + df['Costo Comp ($)']
                                    df['Subtotal Sac'] = df['Costo Sac Int ($)'] + df['Costo Sac Comp ($)']
                                    df['Subtotal Ing'] = df['Ingreso Int ($)'] + df['Ingreso Comp ($)']
                                    
                                    cols_ordenadas = [
                                        'Semana',
                                        'Costo Int ($)', 'Costo Comp ($)', 'Subtotal Reses',
                                        'Costo Sac Int ($)', 'Costo Sac Comp ($)', 'Subtotal Sac',
                                        'Ingreso Int ($)', 'Ingreso Comp ($)', 'Subtotal Ing'
                                    ]
                                    df_view = df[cols_ordenadas].copy()
                                    
                                    total_row = {'Semana': 'TOTAL'}
                                    for col in cols_ordenadas[1:]:
                                        total_row[col] = df_view[col].sum()
                                        
                                    df_view = pd.concat([df_view, pd.DataFrame([total_row])], ignore_index=True)
                                    df_view = df_view.rename(columns=nombres_descriptivos)
                                    df_view = df_view.set_index('Semana')
                                    
                                    def estilo_financiero_columnas(df_styler):
                                        styler = df_styler.format("${:,.0f}")
                                        cols_costos = [c for c in df_view.columns if 'Costo' in c and 'SUBTOTAL' not in c]
                                        cols_ingresos = [c for c in df_view.columns if 'Ingreso' in c and 'SUBTOTAL' not in c]
                                        cols_subtotales = [c for c in df_view.columns if 'SUBTOTAL' in c]
                                        
                                        # Aplicar colores a las columnas
                                        styler.applymap(lambda x: 'color: #d62728;', subset=cols_costos) # Rojo
                                        styler.applymap(lambda x: 'color: #2ca02c;', subset=cols_ingresos) # Verde
                                        styler.applymap(lambda x: 'font-weight: bold; background-color: #f0f0f0; color: black;', subset=cols_subtotales)
                                        
                                        # CORRECCIÓN AQUÍ: Función para resaltar la fila TOTAL sin usar subset problemático
                                        def highlight_total_row(row):
                                            if row.name == 'TOTAL':
                                                return ['font-weight: bold; border-top: 2px solid black; background-color: #e6e6e6; color: black'] * len(row)
                                            return [''] * len(row)
                            
                                        # Aplicar a todas las filas (axis=1), la lógica interna filtra 'TOTAL'
                                        styler.apply(highlight_total_row, axis=1)
                                        
                                        return styler
                            
                                    return estilo_financiero_columnas(df_view.style)
    
                            # --- Visualización ---
                            if vista_tipo == "Consolidado":
                                df_consolidado = df_zona.groupby('Semana').agg({
                                    'Reses Int': 'sum', 'Reses Comp': 'sum',
                                    'Costo Int ($)': 'sum', 'Costo Comp ($)': 'sum',
                                    'Costo Sac Int ($)': 'sum', 'Costo Sac Comp ($)': 'sum',
                                    'Ingreso Int ($)': 'sum', 'Ingreso Comp ($)': 'sum'
                                }).reset_index()
                                
                                st.subheader(f"📊 Unidades por Semana - {zona_seleccionada}")
                                st.dataframe(generar_tabla_semanas_filas(df_consolidado, "Unidades"), use_container_width=True)
                                
                                st.subheader(f"💰 Costos e Ingresos por Semana - {zona_seleccionada}")
                                st.dataframe(generar_tabla_semanas_filas(df_consolidado, "Financiera"), use_container_width=True)
    
                            else:  # Vista por Planta
                                plantas_disponibles = sorted(df_zona['Planta'].unique())
                                planta_seleccionada = st.selectbox("Seleccionar Planta:", plantas_disponibles, key=f"planta_{zona_seleccionada}")
                                df_planta = df_zona[df_zona['Planta'] == planta_seleccionada]
                                
                                if not df_planta.empty:
                                    st.subheader(f"Resumen Planta {planta_seleccionada}")
                                    c1, c2, c3, c4 = st.columns(4)
                                    c1.metric("Total Reses", f"{df_planta['Reses Int'].sum() + df_planta['Reses Comp'].sum():,.0f}")
                                    c2.metric("Costo Reses", f"${df_planta['Costo Int ($)'].sum() + df_planta['Costo Comp ($)'].sum():,.0f}")
                                    c3.metric("Costo Sacrificio", f"${df_planta['Costo Sac Int ($)'].sum() + df_planta['Costo Sac Comp ($)'].sum():,.0f}")
                                    c4.metric("Ingreso Total", f"${df_planta['Ingreso Int ($)'].sum() + df_planta['Ingreso Comp ($)'].sum():,.0f}")
    
                                    st.subheader(f"📊 Unidades - {planta_seleccionada}")
                                    st.dataframe(generar_tabla_semanas_filas(df_planta, "Unidades"), use_container_width=True)
                                    
                                    st.subheader(f"💰 Costos e Ingresos - {planta_seleccionada}")
                                    st.dataframe(generar_tabla_semanas_filas(df_planta, "Financiera"), use_container_width=True)
                                else:
                                    st.info(f"No hay datos para la planta {planta_seleccionada}")
                
                with tab2:
                    st.subheader("🚚 Análisis de Costos de Transporte por Zona")
                    
                    # Seleccionar zona para análisis de transporte
                    zona_transporte = st.selectbox(
                        "Seleccionar Zona para análisis de transporte:",
                        options=zonas_disponibles,
                        key="zona_transporte_selector"
                    )
                    
                    # Calcular costos de transporte para la zona seleccionada
                    transporte_data = []
                    semanas = contexto['Semana']
                    plantas = contexto['Planta_S']
                    
                    for t in semanas:
                        for p in plantas:
                            viaje_int_var = contexto['variables']['viaje_int'].get((zona_transporte, p, t))
                            viaje_com_var = contexto['variables']['viaje_com'].get((zona_transporte, p, t))
                            
                            viaje_int_val = obtener_valor_pulp(viaje_int_var)
                            viaje_com_val = obtener_valor_pulp(viaje_com_var)
                            
                            if viaje_int_val > 0 or viaje_com_val > 0:
                                costo_viaje_int = contexto['parametros'].get('Costo_Viaje_Int', {}).get((zona_transporte, p), 0)
                                costo_viaje_comp = contexto['parametros'].get('Costo_Viaje_Comp', {}).get((zona_transporte, p), 0)
                                
                                transporte_data.append({
                                    'Semana': t,
                                    'Planta Destino': p,
                                    'Viajes Integrados': int(viaje_int_val),
                                    'Viajes Comprados': int(viaje_com_val),
                                    'Costo por Viaje Int ($)': costo_viaje_int,
                                    'Costo por Viaje Comp ($)': costo_viaje_comp,
                                    'Costo Total Int ($)': viaje_int_val * costo_viaje_int,
                                    'Costo Total Comp ($)': viaje_com_val * costo_viaje_comp
                                })
                    
                    if transporte_data:
                        df_transporte = pd.DataFrame(transporte_data)
                        
                        # Calcular totales
                        total_viajes_int = df_transporte['Viajes Integrados'].sum()
                        total_viajes_comp = df_transporte['Viajes Comprados'].sum()
                        total_costo_int = df_transporte['Costo Total Int ($)'].sum()
                        total_costo_comp = df_transporte['Costo Total Comp ($)'].sum()
                        
                        # Mostrar métricas
                        st.subheader("Resumen de Transporte")
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("Viajes Integrados", f"{total_viajes_int:,.0f}")
                        with col2:
                            st.metric("Viajes Comprados", f"{total_viajes_comp:,.0f}")
                        with col3:
                            st.metric("Costo Transp. Int", f"${total_costo_int:,.0f}")
                        with col4:
                            st.metric("Costo Transp. Comp", f"${total_costo_comp:,.0f}")
                        
                        # Mostrar tabla detallada
                        st.subheader("Detalle por Semana y Planta")
                        st.dataframe(
                            df_transporte.style.format({
                                'Viajes Integrados': '{:,.0f}',
                                'Viajes Comprados': '{:,.0f}',
                                'Costo por Viaje Int ($)': '${:,.0f}',
                                'Costo por Viaje Comp ($)': '${:,.0f}',
                                'Costo Total Int ($)': '${:,.0f}',
                                'Costo Total Comp ($)': '${:,.0f}'
                            }),
                            use_container_width=True,
                            height=300
                        )
                        
                        # Gráfico de costos de transporte por semana
                        st.subheader("Evolución Semanal de Costos de Transporte")
                        
                        if not df_transporte.empty:
                            df_transporte_semanal = df_transporte.groupby('Semana').agg({
                                'Costo Total Int ($)': 'sum',
                                'Costo Total Comp ($)': 'sum'
                            }).reset_index()
                            
                            try:
                                import plotly.express as px
                                df_transporte_semanal_melted = pd.melt(
                                    df_transporte_semanal,
                                    id_vars=['Semana'],
                                    value_vars=['Costo Total Int ($)', 'Costo Total Comp ($)'],
                                    var_name='Tipo Transporte',
                                    value_name='Costo'
                                )
                                
                                fig = px.bar(
                                    df_transporte_semanal_melted,
                                    x='Semana',
                                    y='Costo',
                                    color='Tipo Transporte',
                                    title=f"Costos de Transporte por Semana - {zona_transporte}",
                                    labels={'Costo': 'Costo ($)', 'Semana': 'Semana'},
                                    barmode='group'
                                )
                                fig.update_layout(
                                    yaxis_tickformat=',.0f',
                                    hovermode='x unified'
                                )
                                st.plotly_chart(fig, use_container_width=True)
                            except:
                                # Fallback a gráfico de barras simple
                                chart_data = df_transporte_semanal.set_index('Semana')
                                st.bar_chart(chart_data)
                    else:
                        st.info(f"⚠️ No hay costos de transporte para la zona {zona_transporte} en la solución óptima.")
                
                # Resumen ejecutivo por zona
                st.subheader("📋 Resumen Ejecutivo por Zona")
                
                # Crear resumen para todas las zonas
                resumen_zonas = []
                
                for zona in zonas_disponibles:
                    total_reses_int = 0
                    total_reses_comp = 0
                    total_costo_int = 0
                    total_costo_comp = 0
                    total_costo_transporte = 0
                    
                    for t in semanas:
                        for p in plantas:
                            # Obtener valores con manejo seguro
                            res_int_var = contexto['variables']['res_int'].get((zona, p, t))
                            res_comp_var = contexto['variables']['res_comp'].get((zona, p, t))
                            viaje_int_var = contexto['variables']['viaje_int'].get((zona, p, t))
                            viaje_com_var = contexto['variables']['viaje_com'].get((zona, p, t))
                            
                            res_int_val = obtener_valor_pulp(res_int_var)
                            res_comp_val = obtener_valor_pulp(res_comp_var)
                            viaje_int_val = obtener_valor_pulp(viaje_int_var)
                            viaje_com_val = obtener_valor_pulp(viaje_com_var)
                            
                            total_reses_int += res_int_val
                            total_reses_comp += res_comp_val
                            
                            # Costos
                            precio_int = contexto['parametros']['Precio_Int'].get(zona, 0)
                            precio_comp = contexto['parametros']['Precio_Comp'].get(zona, 0)
                            costo_viaje_int = contexto['parametros'].get('Costo_Viaje_Int', {}).get((zona, p), 0)
                            costo_viaje_comp = contexto['parametros'].get('Costo_Viaje_Comp', {}).get((zona, p), 0)
                            
                            total_costo_int += res_int_val * precio_int
                            total_costo_comp += res_comp_val * precio_comp
                            total_costo_transporte += viaje_int_val * costo_viaje_int
                            total_costo_transporte += viaje_com_val * costo_viaje_comp
                    
                    resumen_zonas.append({
                        'Zona': zona,
                        'Reses Integradas': total_reses_int,
                        'Reses Compradas': total_reses_comp,
                        'Total Reses': total_reses_int + total_reses_comp,
                        'Costo Integración ($)': total_costo_int,
                        'Costo Compras ($)': total_costo_comp,
                        'Costo Transporte ($)': total_costo_transporte,
                        'Costo Total ($)': total_costo_int + total_costo_comp + total_costo_transporte
                    })
                
                df_resumen_zonas = pd.DataFrame(resumen_zonas)
                
                # Mostrar resumen
                st.dataframe(
                    df_resumen_zonas.style.format({
                        'Reses Integradas': '{:,.0f}',
                        'Reses Compradas': '{:,.0f}',
                        'Total Reses': '{:,.0f}',
                        'Costo Integración ($)': '${:,.0f}',
                        'Costo Compras ($)': '${:,.0f}',
                        'Costo Transporte ($)': '${:,.0f}',
                        'Costo Total ($)': '${:,.0f}'
                    }).background_gradient(subset=['Total Reses', 'Costo Total ($)'], cmap='Blues'),
                    use_container_width=True,
                    height=400
                )
else:
    st.info("Por favor cargue un archivo Excel con los parámetros del modelo en el panel lateral")

# Plantilla de Excel (opcional)
with st.expander("Descargar plantilla de Excel"):
    st.write("""
    Descargue esta plantilla y complétela con sus datos antes de cargarla en la aplicación.
    La plantilla debe contener las siguientes hojas:
    
    - **Oferta**: Disponibilidad de reses integradas por zona y semana
    - **Compras**: Disponibilidad de reses a comprar por zona y semana
    - **Demanda**: Demanda semanal de reses
    - **CV_PDN**: Costo variable de sacrificio por planta
    - **CTransporteZF**: Costo de transporte de reses integradas
    - **CTransporteZFC**: Costo de transporte de reses compradas
    - **CTransporteE**: Costo de transporte de canales
    - **Cap_Planta**: Capacidad de sacrificio por planta
    - **CR_INTEGRADA**: Valor de reses integradas por zona
    - **CR_COMPRADA**: Valor de reses compradas por zona
    - **RENDIMIENTO**: Rendimiento por zona y planta
    - **PRECIOKG**: Precio por kg por zona
    - **PESORES**: Peso de res por zona
    """)
    
    # Crear archivo Excel de ejemplo en memoria
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
       
        Zonas = ['ANTIOQUIA','VALLEDUPAR','COSTA','MAGDALENA MEDIO', 'LLANOS', 'SUR DEL CESAR', 'MAGDALENA MEDIO NORTE']
        Semanas = ['27.2025', '28.2025', '29.2025', '30.2025']
        Plantas = ['AGUACHICA','FRIGOSINU','CENTRAL GANADERA','FRIOGAN DORADA','COROZAL']
        # Hoja de ejemplo para Oferta
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Semanas],
            'SEMANA': Semanas * len(Zonas),
            'OFERTA': 25
        }).to_excel(writer, sheet_name='Oferta', index=False)
        
        # Hoja de ejemplo para Demanda
        pd.DataFrame({
            'SEMANA': Semanas,
            'DEMANDA': 100
        }).to_excel(writer, sheet_name='Demanda', index=False)
        
        # Hoja de ejemplo para CV_PDN
        pd.DataFrame({
            'PLANTA': Plantas,
            'CV_PDN': 130000
        }).to_excel(writer, sheet_name='CV_PDN', index=False)
        
        # Hoja de ejemplo para Costos de transporte de zonas a plantas integradas
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'C_TRANS_ZF': 1200000
        }).to_excel(writer, sheet_name='CTransporteZF', index=False)
        
        # Hoja de ejemplo para RENDIMIENTO
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'RDTO': 0.55
        }).to_excel(writer, sheet_name='RENDIMIENTO', index=False)
        # Hoja de ejemplo para Diponibilidad de compra
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Semanas],
            'SEMANA': Semanas * len(Zonas),
            'DISPONIBLE': 25
        }).to_excel(writer, sheet_name='Compras', index=False)
        # Hoja de ejemplo para Capacidad de planta
        pd.DataFrame({
            'PLANTA': Plantas,
            'CAP_PLANTA': 50
        }).to_excel(writer, sheet_name='Cap_Planta', index=False) 
        # Hoja de ejemplo para Costos de transporte de zonas a plantas de reses compradas
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'C_TRANS_ZF': 1200000
        }).to_excel(writer, sheet_name='CTransporteZFC', index=False)   
        # Hoja de ejemplo para Costos de transporte de plantas a Envigado
        pd.DataFrame({
            'PLANTA': Plantas,
            'C_TRANS_E': 4000000
        }).to_excel(writer, sheet_name='CTransporteE', index=False) 
        #Hoja de ejemplo para el promedio de peso de reses en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'PESO': 400
        }).to_excel(writer,sheet_name='PESORES',index=False)
        #Hoja de ejemplo para el precio por kg negociado en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'PRECIO': 8000
        }).to_excel(writer,sheet_name='PRECIOKG',index=False)
        #Hoja de ejemplo para el Costo de reses compradas en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'PRECIO': 2500000
        }).to_excel(writer,sheet_name='CR_CROMPRADA',index=False)
        #Hoja de ejemplo para el Costo de reses integradas en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'PRECIO': 1500000
        }).to_excel(writer,sheet_name='CR_INTEGRADA',index=False)     
    
    st.download_button(
        label="Descargar plantilla",
        data=output.getvalue(),
        file_name="plantilla_sacrificio_reses.xlsx",
        mime="application/vnd.ms-excel"

    )
