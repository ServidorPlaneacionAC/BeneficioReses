import streamlit as st
import pandas as pd
from pulp import *
from io import BytesIO
import time
import math # Necesario para calculos matematicos

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
            # 1. Leer la hoja de Excel y guardarla en la variable 'df'
            df = pd.read_excel(excel_data, sheet_name=sheet_name)
            
            # 2. Limpiar espacios en blanco accidentales en los nombres de las columnas
            df.columns = df.columns.str.strip()
            
            # 3. Guardar la hoja ya limpia en el diccionario de resultados
            dfs[sheet_name] = df
            
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

def crear_diccionario_rdto_total(inputs_opt_res):
    # Obtener DataFrames
    df_merma_tte = inputs_opt_res['MERMA.TTE.ZONAPLANTA'].copy()
    df_mermas_plantas = inputs_opt_res['MERMA.PLANTA'].copy()
    
    # Fusionar los DataFrames
    df_merged = pd.merge(
        df_merma_tte,
        df_mermas_plantas[['PLANTA', 'R_CANAL_CALIENTE', 'M_FRIO']],
        on='PLANTA',
        how='left'
    )
    
    # Calcular rendimiento total
    df_merged['RENDIMIENTO'] = (
        (1 - df_merged['MERMA']) * (df_merged['R_CANAL_CALIENTE'].fillna(0)) * (1 - df_merged['M_FRIO'].fillna(0))
    )
    
    # Crear diccionario
    rdto_total = dict(zip(
        zip(df_merged['ZONA'], df_merged['PLANTA']),
        df_merged['RENDIMIENTO']
    ))
    
    return rdto_total

# Función principal del modelo
def ejecutar_modelo(inputs_opt_res, valor_kg, MinCompra, MinAgua):
    try:
        # Definición de conjuntos
        Zona = list(set(inputs_opt_res['OFT.INTEGRADAS']['ZONA']))
        Planta_S = list(set(inputs_opt_res['C.VARIABLE.PDN']['PLANTA']))
        Semana = list(set(inputs_opt_res['DDA.CANALES']['SEMANA']))

        # Definición de parámetros
        Demanda = crear_diccionario(inputs_opt_res['DDA.CANALES'], ['SEMANA'], 'DEMANDA')
        Oferta_Int = crear_diccionario(inputs_opt_res['OFT.INTEGRADAS'], ['ZONA','SEMANA'], 'DISPONIBLE')
        Oferta_Com = crear_diccionario(inputs_opt_res['OFT.COMPRADAS'], ['ZONA','SEMANA'], 'DISPONIBLE')
        Precio_Sac = crear_diccionario(inputs_opt_res['C.VARIABLE.PDN'], ['PLANTA'], 'C_BENEFICIO')
        Retoma_Sac = crear_diccionario(inputs_opt_res['C.VARIABLE.PDN'], ['PLANTA'], 'RETOMAS')
        Costo_Sac = {k: Precio_Sac[k] - Retoma_Sac[k] for k in Precio_Sac}
        Costo_Viaje_Int = crear_diccionario(inputs_opt_res['C.TTE.INT.ZONAPLANTA'], ['ZONA','PLANTA'], 'C_TRANS_ZONAPLANTA')
        Costo_Viaje_Comp = crear_diccionario(inputs_opt_res['C.TTE.COMP.ZONAPLANTA'], ['ZONA','PLANTA'], 'C_TRANS_ZONAPLANTA')
        Costo_Tans_PT = crear_diccionario(inputs_opt_res['C.TTE.ENVIGADO'], ['PLANTA'], 'C_TRANS_ENVIGADO')
        Capacidad = crear_diccionario(inputs_opt_res['CAP.PLANTA'], ['PLANTA'], 'CAP_PLANTA')
        Peso_Res = crear_diccionario(inputs_opt_res['PESO.RES'], ['ZONA'], 'PESO')
        rdto = crear_diccionario_rdto_total(inputs_opt_res)
        Precio_Kg_Int = crear_diccionario(inputs_opt_res['PRECIO.KG'], ['ZONA'], 'INTEGRACION')
        Precio_Kg_comp = crear_diccionario(inputs_opt_res['PRECIO.KG'], ['ZONA'], 'COMPRAS')
        
        Precio_Int = {k: Precio_Kg_Int[k] * Peso_Res[k] for k in Precio_Kg_Int}
        Precio_Comp = {k: Precio_Kg_comp[k] * Peso_Res[k] for k in Precio_Kg_comp}
        

        costo_f = crear_diccionario(inputs_opt_res['C.FIJO.AGUACHICA'], ['PLANTA'], 'COSTO_F_SEMANAL')
        
        # Creación del modelo
        modelo = LpProblem("CostoSacrificio", LpMaximize)

        # Variables de decisión
        res_int = LpVariable.dicts('res_int', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        res_comp = LpVariable.dicts('res_comp', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_int = LpVariable.dicts('viaje_Int_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_com = LpVariable.dicts('viaje_Com_zona', [(z,p,t) for z in Zona for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        viaje_envigado = LpVariable.dicts('viaje_envigado', [(p,t) for p in Planta_S for t in Semana], lowBound=0, cat='Integer')
        compra_res = LpVariable.dicts('compra_res',[(z,p,t) for z in Zona for p in Planta_S for t in Semana],cat='Binary')

        # Función objetivo
        modelo += (lpSum(res_int[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg for z in Zona for p in Planta_S for t in Semana) +
                   lpSum(res_comp[z,p,t] * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(res_int[z,p,t] * Precio_Int.get((z),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(res_comp[z,p,t] * Precio_Comp.get((z),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(res_int[z,p,t] * Costo_Sac.get((p),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(res_comp[z,p,t] * Costo_Sac.get((p),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(viaje_int[z,p,t] * Costo_Viaje_Int.get((z,p),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(viaje_com[z,p,t] * Costo_Viaje_Comp.get((z,p),0) for z in Zona for p in Planta_S for t in Semana) -
                   lpSum(viaje_envigado[p,t] * Costo_Tans_PT.get((p),0) for p in Planta_S for t in Semana)
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

        for t in Semana:
            modelo += (lpSum(res_int[z,'AGUACHICA',t] for z in Zona) + lpSum(res_comp[z,'AGUACHICA',t] for z in Zona)) >= MinAgua

                
        # Resolver el modelo
        modelo.solve(PULP_CBC_CMD(timeLimit=180))
        
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
                # --- AGREGADOS LOS COSTOS DE TRANSPORTE FALTANTES ---
                'Costo_Viaje_Int': Costo_Viaje_Int,
                'Costo_Viaje_Comp': Costo_Viaje_Comp,
                'Costo_Tans_PT': Costo_Tans_PT,
                # ----------------------------------------------------
                'Peso_Res': Peso_Res,
                'rdto': rdto,
                'valor_kg': valor_kg,
                'Demanda': Demanda,
                'Oferta_Int': Oferta_Int,
                'Oferta_Com': Oferta_Com,
                'Capacidad': Capacidad,
                'Costo_F': costo_f  # Costos Fijos Agregados
            }
        }
        
        # Calcular métricas de costos
        # --- BLOQUE CORREGIDO PARA CALCULAR COSTOS ---
        # 1. Calcular cada componente por separado para asegurar precisión
        val_costo_int = sum((res_int[z,p,t].varValue or 0) * Precio_Int.get((z),0) 
                            for z in Zona for p in Planta_S for t in Semana)
        
        val_costo_comp = sum((res_comp[z,p,t].varValue or 0) * Precio_Comp.get((z),0) 
                             for z in Zona for p in Planta_S for t in Semana)
        
        val_costo_sac = (sum(((res_int[z,p,t].varValue or 0) * Costo_Sac.get((p),0))
                             for z in Zona for p in Planta_S for t in Semana) +
                         sum(((res_comp[z,p,t].varValue or 0) * Costo_Sac.get((p),0))
                             for z in Zona for p in Planta_S for t in Semana))
        
        val_costo_tte_res = (sum(((viaje_int[z,p,t].varValue or 0) * Costo_Viaje_Int.get((z,p),0))
                                 for z in Zona for p in Planta_S for t in Semana) +
                             sum(((viaje_com[z,p,t].varValue or 0) * Costo_Viaje_Comp.get((z,p),0))
                                 for z in Zona for p in Planta_S for t in Semana))
        
        val_costo_tte_pt = sum((viaje_envigado[p,t].varValue or 0) * Costo_Tans_PT.get((p),0) 
                               for p in Planta_S for t in Semana)
        
        val_carne = (sum(((res_int[z,p,t].varValue or 0) * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg)
                         for z in Zona for p in Planta_S for t in Semana) +
                     sum(((res_comp[z,p,t].varValue or 0) * Peso_Res.get((z),0) * rdto.get((z,p),0) * valor_kg)
                         for z in Zona for p in Planta_S for t in Semana))

        # 2. Calcular la Valorización Total como una RESTA simple (Ingreso - Costos)
        # Esto garantiza que el valor coincida visualmente con la tabla
        total_costos = (val_costo_int + val_costo_comp + val_costo_sac + 
                        val_costo_tte_res + val_costo_tte_pt)
                        
        val_valorizacion = val_carne - total_costos

        # 3. Construir el diccionario final
        costos = {
            'Costo Integración': val_costo_int,
            'Costo Compras': val_costo_comp,
            'Costo Sacrificio': val_costo_sac,
            'Costo Transporte Reses': val_costo_tte_res,
            'Costo Transporte Canales': val_costo_tte_pt,
            'Valor Carne': val_carne,
            'Valorización Total': val_valorizacion  # <--- Aquí está la corrección clave
        }
        # -----------------------------------------------------------
        
        # --- CALCULO DE KPIs PARA EL COSTO POR KG (NO AFECTA EL MODELO) ---
        # 1. Total Kilos producidos (Ingreso Total / Precio Venta)
        total_kg_producidos = val_carne / valor_kg if valor_kg > 0 else 0
        
        # 2. Total Costo Fijo (Solo plantas usadas * Numero de Semanas)
        plantas_usadas = set()
        for z in Zona:
            for p in Planta_S:
                for t in Semana:
                    # Si se envió alguna res (integrada o comprada) a esa planta, se marca como usada
                    if (res_int[z,p,t].varValue or 0) > 0 or (res_comp[z,p,t].varValue or 0) > 0:
                        plantas_usadas.add(p)
        
        num_semanas = len(Semana)
        # Sumamos el costo fijo semanal de cada planta usada y lo multiplicamos por las semanas
        total_costo_fijo = sum(costo_f.get(p, 0) * num_semanas for p in plantas_usadas)
        
        # Guardar estos datos en 'KPIs' para usarlos luego en la tabla comparativa
        contexto['KPIs'] = {
            'Total Kg': total_kg_producidos,
            'Total Costo Fijo': total_costo_fijo
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
    MinCompra = st.number_input("Cantidad mínima viable para compra de reses", min_value=0.0, value=14.0, step=1.0)
    MinAgua = st.number_input("Cantidad mínima viable para beneficiar en Aguachica", min_value=0.0, value=1400.0, step=1.0)
        
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
                modelo, contexto, costos = ejecutar_modelo(current_data, valor_kg, MinCompra, MinAgua)
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
                st.dataframe(df_consolidado.style.format({'Semana': '{:.2f}'}))
                
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
            
            # Mostrar  (se mantiene igual)
            st.subheader("Desglose de Costos y Valores")
            df_costos = pd.DataFrame.from_dict(costos, orient='index', columns=['Valor ($)'])
            st.dataframe(df_costos.style.format("{:,.0f}"))

            def calcular_escenario_hipotetico_detallado(contexto, planta_objetivo="AGUACHICA"):
                """
                Calcula los costos si todo se enviara a una sola planta.
                CORRECCIÓN: Agrupa reses por (Zona, Semana) antes de calcular camiones para optimizar el flete.
                """
                import math

                # Inicializar acumuladores
                acumuladores = {
                    'Costo Integración': 0,
                    'Costo Compras': 0,
                    'Costo Sacrificio': 0,
                    'Costo Transporte Reses': 0,
                    'Costo Transporte Canales': 0,
                    'Valor Carne': 0
                }
                
                # Estructuras para agrupar volumenes totales por (Zona, Semana)
                # Separamos Integradas vs Compradas por si tienen contratos de flete distintos
                volumen_int = {}   # Clave: (zona, semana) -> Valor: cantidad_total
                volumen_comp = {}  # Clave: (zona, semana) -> Valor: cantidad_total
                
                total_reses_procesadas = 0
                total_kg_producidos = 0
                
                # Definir P explícitamente para acceder a los parametros
                P = contexto['parametros']
                
                # Parametros directos
                P_Int = P['Precio_Int']
                P_Comp = P['Precio_Comp']
                C_Viaje_Int = P['Costo_Viaje_Int']
                C_Viaje_Comp = P['Costo_Viaje_Comp']
                C_Sac = P['Costo_Sac']
                Peso = P['Peso_Res']
                Rendimiento = P['rdto']
                Val_Kg = P['valor_kg']
                
                if planta_objetivo not in C_Sac: return None

                # -------------------------------------------------------
                # PASO 1: ACUMULAR COSTOS DIRECTOS Y VOLUMENES (Sin flete aún)
                # -------------------------------------------------------
                
                # Reses Integradas
                for (z, p, t), var in contexto['variables']['res_int'].items():
                    if var.varValue and var.varValue > 0:
                        qty = var.varValue
                        total_reses_procesadas += qty
                        
                        # Costos que son por unidad (independiente del camión)
                        acumuladores['Costo Integración'] += qty * P_Int.get(z, 0)
                        acumuladores['Costo Sacrificio'] += qty * C_Sac.get(planta_objetivo, 0)
                        
                        rdto_agua = Rendimiento.get((z, planta_objetivo), 0)
                        kg_carne = qty * Peso.get(z, 0) * rdto_agua
                        acumuladores['Valor Carne'] += kg_carne * Val_Kg
                        total_kg_producidos += kg_carne
                        
                        # Agrupar volumen para calcular camiones después
                        if (z, t) not in volumen_int: volumen_int[(z, t)] = 0
                        volumen_int[(z, t)] += qty

                # Reses Compradas
                for (z, p, t), var in contexto['variables']['res_comp'].items():
                    if var.varValue and var.varValue > 0:
                        qty = var.varValue
                        total_reses_procesadas += qty
                        
                        # Costos por unidad
                        acumuladores['Costo Compras'] += qty * P_Comp.get(z, 0)
                        acumuladores['Costo Sacrificio'] += qty * C_Sac.get(planta_objetivo, 0)
                        
                        rdto_agua = Rendimiento.get((z, planta_objetivo), 0)
                        kg_carne = qty * Peso.get(z, 0) * rdto_agua
                        acumuladores['Valor Carne'] += kg_carne * Val_Kg
                        total_kg_producidos += kg_carne
                        
                        # Agrupar volumen
                        if (z, t) not in volumen_comp: volumen_comp[(z, t)] = 0
                        volumen_comp[(z, t)] += qty

                # -------------------------------------------------------
                # PASO 2: CALCULAR CAMIONES Y FLETES (Lógica optimizada)
                # -------------------------------------------------------
                
                # Para Integradas: Sumamos todo lo de una zona/semana y ahí pedimos los camiones
                for (z, t), cantidad_total in volumen_int.items():
                    # Ahora sí: Total reses de la zona / 14
                    viajes = math.ceil(cantidad_total / 14)
                    costo_viaje = C_Viaje_Int.get((z, planta_objetivo), 0)
                    acumuladores['Costo Transporte Reses'] += viajes * costo_viaje

                # Para Compradas
                for (z, t), cantidad_total in volumen_comp.items():
                    viajes = math.ceil(cantidad_total / 14)
                    costo_viaje = C_Viaje_Comp.get((z, planta_objetivo), 0)
                    acumuladores['Costo Transporte Reses'] += viajes * costo_viaje

                # -------------------------------------------------------
                # PASO 3: TRANSPORTE DE SALIDA (CANALES)
                # -------------------------------------------------------
                # Asumiendo 84 canales por camión refrigerado
                viajes_canales = math.ceil(total_reses_procesadas / 84) if total_reses_procesadas > 0 else 0
                acumuladores['Costo Transporte Canales'] = viajes_canales * contexto['parametros']['Costo_Tans_PT'].get(planta_objetivo, 0)

                # Finalizar totales
                costos_totales = sum([v for k, v in acumuladores.items() if 'Costo' in k])
                acumuladores['Valorización Total'] = acumuladores['Valor Carne'] - costos_totales
                
                # Costo Fijo (Solo Aguachica * Semanas)
                num_semanas = len(contexto['Semana'])
                # Usamos .get para evitar errores si no existe Costo_F en datos viejos
                costos_fijos_dict = P.get('Costo_F', {})
                total_costo_fijo = costos_fijos_dict.get(planta_objetivo, 0) * num_semanas
                
                # Guardar KPIs
                acumuladores['Total Kg'] = total_kg_producidos
                acumuladores['Total Costo Fijo'] = total_costo_fijo
                
                return acumuladores

            # ==============================================================================
            # BLOQUE DE EJECUCIÓN Y VISUALIZACIÓN (Pegar justo después de la función)
            # ==============================================================================

            # 1. Ejecutar el cálculo del escenario hipotético
            escenario_b = calcular_escenario_hipotetico_detallado(contexto, "AGUACHICA")

            # --- COMPARATIVO DE ESCENARIOS (CON COSTO FINAL POR KG) ---
            if escenario_b:
                st.markdown("---")
                st.subheader("⚖️ Comparativo de Escenarios: Óptimo vs. Todo a Aguachica")
                
                # Datos Escenario Optimo (Recuperamos lo calculado en el modelo)
                kpis_opt = contexto.get('KPIs', {'Total Kg': 0, 'Total Costo Fijo': 0})
                total_costo_var_opt = costos['Valor Carne'] - costos['Valorización Total']
                # Costo Total Real = Variables + Fijos
                total_costo_total_opt = total_costo_var_opt + kpis_opt['Total Costo Fijo']
                # DIVISIÓN CLAVE: Costo Total / Kilos Totales
                costo_final_kg_opt = total_costo_total_opt / kpis_opt['Total Kg'] if kpis_opt['Total Kg'] > 0 else 0
                
                # Datos Escenario Aguachica (Recuperamos lo calculado en la función hipotética)
                total_costo_var_agua = escenario_b['Valor Carne'] - escenario_b['Valorización Total']
                total_costo_total_agua = total_costo_var_agua + escenario_b['Total Costo Fijo']
                # DIVISIÓN CLAVE
                costo_final_kg_agua = total_costo_total_agua / escenario_b['Total Kg'] if escenario_b['Total Kg'] > 0 else 0

                data_unificada = []
                # Filas normales (Variables)
                for concepto in list(costos.keys()):
                    val_opt = costos[concepto]
                    val_agua = escenario_b.get(concepto, 0)
                    diff = val_opt - val_agua
                    pct = (diff / val_agua) if val_agua != 0 else 0.0
                    
                    # Visual: Costos negativos para que se vea como egreso
                    es_costo = 'Costo' in concepto
                    val_opt_visual = val_opt * -1 if es_costo else val_opt
                    val_agua_visual = val_agua * -1 if es_costo else val_agua
                    
                    data_unificada.append({
                        'Concepto': concepto,
                        'Escenario Óptimo': val_opt_visual,
                        'Escenario Aguachica': val_agua_visual,
                        'Diferencia ($)': diff,
                        'Var. (%)': pct
                    })
                    
                val_kg_opt = kpis_opt['Total Kg']
                val_kg_agua = escenario_b['Total Kg']
                
                diff_kg_total = val_kg_opt - val_kg_agua
                pct_kg_total = (diff_kg_total / val_kg_agua) if val_kg_agua != 0 else 0.0

                data_unificada.append({
                    'Concepto': 'Total Kg Aprovechados',
                    'Escenario Óptimo': val_kg_opt, 
                    'Escenario Aguachica': val_kg_agua,
                    'Diferencia ($)': diff_kg_total,
                    'Var. (%)': pct_kg_total
                })
                # --- AGREGAR LA FILA FINAL A LA TABLA ---
                diff_kg = costo_final_kg_opt - costo_final_kg_agua
                pct_kg = (diff_kg / costo_final_kg_agua) if costo_final_kg_agua != 0 else 0
                data_unificada.append({
                    'Concepto': 'Costo Final por Kg (con costo fijo planta Aguachica)', 
                    'Escenario Óptimo': costo_final_kg_opt * -1,  # Visualmente negativo
                    'Escenario Aguachica': costo_final_kg_agua * -1, 
                    'Diferencia ($)': diff_kg, 
                    'Var. (%)': pct_kg
                })
                
                df_comparativo = pd.DataFrame(data_unificada)

                def estilo_comparativo_final(df_styler):
                    # 1. Aplicamos formato de Moneda ($) a TODO por defecto
                    styler = df_styler.format({
                        'Escenario Óptimo': '${:,.0f}',
                        'Escenario Aguachica': '${:,.0f}',
                        'Diferencia ($)': '${:,.0f}',
                        'Var. (%)': '{:.2%}'
                    })
                    
                    # 2. CORRECCIÓN: Formato especial para la fila de 'Total Kg'
                    # Le decimos explícitamente: "Los valores son números normales, PERO la variación sigue siendo %"
                    fila_kg = df_comparativo.index[df_comparativo['Concepto'].str.contains("Total Kg")].tolist()
                    
                    if fila_kg:
                        styler.format(
                            {
                                'Escenario Óptimo': '{:,.0f}',      # Sin signo $
                                'Escenario Aguachica': '{:,.0f}',   # Sin signo $
                                'Diferencia ($)': '{:,.0f}',        # Sin signo $
                                'Var. (%)': '{:.2%}'                # <--- ESTA LÍNEA ASEGURA QUE SE VEA COMO %
                            }, 
                            subset=pd.IndexSlice[fila_kg, :] # Aplica solo a esa fila
                        )

                    # 3. Lógica de Colores
                    def color_var(val, concepto):
                        # Agregamos 'Total Kg' a la lógica de "Más es mejor" (Verde)
                        if 'Valorización' in concepto or 'Valor Carne' in concepto or 'Total Kg' in concepto:
                            color = '#2ca02c' if val > 0 else '#d62728'
                        else:
                            # Para costos: Menos es mejor (Verde si es negativo)
                            color = '#2ca02c' if val < 0 else '#d62728'
                        return f'color: {color}; font-weight: bold'

                    styler.apply(lambda x: [color_var(x['Var. (%)'], x['Concepto']) if col == 'Var. (%)' else '' for col in x.index], axis=1)
                    
                    # Negritas y fondos
                    styler.apply(lambda x: ['background-color: #f0f0f0; font-weight: bold' if x['Concepto'] == 'Valorización Total' else '' for _ in x], axis=1)
                    styler.apply(lambda x: ['background-color: #e6f3ff; font-weight: bold; border-top: 2px solid #000' if 'Costo Final' in x['Concepto'] else '' for _ in x], axis=1)
                    
                    return styler

                st.dataframe(estilo_comparativo_final(df_comparativo.style), use_container_width=True)
                
                mejora = costos['Valorización Total'] - escenario_b['Valorización Total']
                st.info(f"💡 **Análisis:** La optimización genera un beneficio adicional de **${mejora:,.0f}** comparado con enviar todo a Aguachica.")
            else:
                st.warning("No se pudo calcular el escenario de Aguachica. Verifique que la planta exista en los parámetros.")
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
                            
                            # Función auxiliar para métricas personalizadas (letra más pequeña y sin truncar)
                            def metrica_personalizada(label, value):
                                st.markdown(f"""
                                <div style="background-color: #f8f9fa; padding: 10px; border-radius: 5px; border: 1px solid #e9ecef;">
                                    <p style="margin: 0; font-size: 14px; color: #6c757d;">{label}</p>
                                    <p style="margin: 0; font-size: 18px; font-weight: 600; color: #212529; word-wrap: break-word;">{value}</p>
                                </div>
                                """, unsafe_allow_html=True)

                            # Cambiamos a 3 columnas (Eliminamos Ingreso Total y damos más espacio)
                            col_a, col_b, col_c = st.columns(3)
                            
                            total_integradas = df_zona['Reses Int'].sum()
                            total_compradas = df_zona['Reses Comp'].sum()
                            total_costo_reses = df_zona['Costo Int ($)'].sum() + df_zona['Costo Comp ($)'].sum()
                            
                            with col_a:
                                metrica_personalizada("Reses Integradas", f"{total_integradas:,.0f}")
                            
                            with col_b:
                                metrica_personalizada("Reses Compradas", f"{total_compradas:,.0f}")
                            
                            with col_c:
                                metrica_personalizada("Costo Total Reses", f"${total_costo_reses:,.0f}")
                            
                            # Espacio separador antes de las tablas
                            st.markdown(" ")
                            
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
                                
                                # Formateamos a 2 decimales solo para la visualización, el orden previo ya se hizo numéricamente
                                df['Semana'] = df['Semana'].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else str(x))
                                
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
                                        styler.map(lambda x: 'color: #d62728;', subset=cols_costos) # Rojo
                                        styler.map(lambda x: 'color: #2ca02c;', subset=cols_ingresos) # Verde
                                        styler.map(lambda x: 'font-weight: bold; background-color: #f0f0f0; color: black;', subset=cols_subtotales)
                                        
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
                                    
                                    c1, c2, c3 = st.columns(3)
                                    total_reses = df_planta['Reses Int'].sum() + df_planta['Reses Comp'].sum()
                                    total_costo_reses = df_planta['Costo Int ($)'].sum() + df_planta['Costo Comp ($)'].sum()
                                    total_costo_sacrificio = df_planta['Costo Sac Int ($)'].sum() + df_planta['Costo Sac Comp ($)'].sum()
                                    with c1:
                                        metrica_personalizada("Reses sacrificadas", f"{total_reses:,.0f}")
                                    
                                    with c2:
                                        metrica_personalizada("Costo de reses", f"{total_costo_reses:,.0f}")
                                    
                                    with c3:
                                        metrica_personalizada("Costo de sacrificio", f"${total_costo_sacrificio:,.0f}")
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
                                'Semana': '{:.2f}',  # <--- COMO YA ES NÚMERO, ESTO FUNCIONARÁ PERFECTO
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
                    total_disponible_int = 0
                    
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

                        # Calcular reses disponibles integradas para esta zona y semana
                        # Acceder a Oferta_Int del contexto
                        if 'Oferta_Int' in contexto['parametros']:
                            oferta_semana = contexto['parametros']['Oferta_Int'].get((zona, t), 0)
                            total_disponible_int += oferta_semana
                        else:
                            # Si no está en parámetros, intentar calcular desde datos originales
                            oferta_semana = 0
                            total_disponible_int += oferta_semana
    
                    # Calcular porcentaje de utilización de reses integradas
                    porcentaje_utilizacion_int = (total_reses_int / total_disponible_int * 100) if total_disponible_int > 0 else 0
                    
                    resumen_zonas.append({
                        'Zona': zona,
                        'Reses Disponibles Int.': int(total_disponible_int),  # Nueva columna
                        'Reses Integradas': int(total_reses_int),
                        '% Utilización Int.': round(porcentaje_utilizacion_int, 1),  # Nueva columna
                        'Reses Compradas': int(total_reses_comp),
                        'Total Reses': int(total_reses_int + total_reses_comp),
                        'Costo Integración ($)': round(total_costo_int, 0),
                        'Costo Compras ($)': round(total_costo_comp, 0),
                        'Costo Transporte ($)': round(total_costo_transporte, 0),
                        'Costo Total ($)': round(total_costo_int + total_costo_comp + total_costo_transporte, 0)
    
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
    
    - **OFT.INTEGRADAS**: Disponibilidad de reses integradas por zona y semana.
    - **OFT.COMPRADAS**: Disponibilidad de reses para comprar por zona y semana.
    - **DDA.CANALES**: Demanda semanal de reses o canales. 
    - **C.VARIABLE.PDN**: Costo variable de beneficio en cada planta.
    - **C.FIJO.AGUACHICA**: Costo fijo semanal de la planta Aguachica.
    - **C.TTE.INT.ZONAPLANTA**: Costo de transporte de reses integradas desde las zonas a las plantas de beneficio.
    - **C.TTE.COMP.ZONAPLANTA**: Costo de transporte de reses compradas desde las zonas a las plantas de beneficio.
    - **C.TTE.ENVIGADO**: Costo de transporte de canales desde cada planta hacia Envigado.
    - **CAP.PLANTA**: Capacidad semanal de sacrificio por planta.
    - **PRECIO.KG**: Precio por kg por zona segregado para reses integradas y compradas.
    - **PESO.RES**: Peso promedio de res por zona.
    - **MERMA.TTE.ZONAPLANTA**: Merma de transporte para cada zona y planta.
    - **MERMA.PLANTA**: Mermas de canal caliente y canal frío en cada planta.
    """)
    
    # Crear archivo Excel de ejemplo en memoria
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        Zonas = ['ANTIOQUIA','VALLEDUPAR','COSTA','MAGDALENA MEDIO', 'LLANOS', 'SUR DEL CESAR', 'MAGDALENA MEDIO NORTE']
        Semanas = ['2026.10', '2026.11', '2026.12', '2026.13']
        Plantas = ['AGUACHICA','FRIGOSINU','CENTRAL GANADERA','FRIOGAN DORADA','COROZAL']
        
        # Hoja de ejemplo para OFT.INTEGRADAS
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Semanas],
            'SEMANA': Semanas * len(Zonas),
            'DISPONIBLE': 25
        }).to_excel(writer, sheet_name='OFT.INTEGRADAS', index=False)

        # Hoja de ejemplo para OFT.COMPRADAS
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Semanas],
            'SEMANA': Semanas * len(Zonas),
            'DISPONIBLE': 25
        }).to_excel(writer, sheet_name='OFT.COMPRADAS', index=False)
        
        # Hoja de ejemplo para DDA.CANALES
        pd.DataFrame({
            'SEMANA': Semanas,
            'DEMANDA': 100
        }).to_excel(writer, sheet_name='DDA.CANALES', index=False)
        
        # Hoja de ejemplo para C.VARIABLE.PDN
        pd.DataFrame({
            'PLANTA': Plantas,
            'C_BENIFICIO': 130000,
            'RETOMAS': 1000
        }).to_excel(writer, sheet_name='C.VARIABLE.PDN', index=False)

        # Hoja de ejemplo para C.FIJO.AGUACHICA 
        pd.DataFrame({
            'PLANTA': Plantas[0],
            'COSTO_F_SEMANAL': 316731916
        }, index=[0]).to_excel(writer, sheet_name='C.FIJO.AGUACHICA', index=False)
        
        # Hoja de ejemplo para Costos de transporte de zonas a plantas reses integradas
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'C_TRANS_ZONAPLANTA': 1200000
        }).to_excel(writer, sheet_name='C.TTE.INT.ZONAPLANTA', index=False)

        # Hoja de ejemplo para Costos de transporte de zonas a plantas reses compradas
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'C_TRANS_ZONAPLANTA': 1200000
        }).to_excel(writer, sheet_name='C.TTE.COMP.ZONAPLANTA', index=False)
        
        # Hoja de ejemplo para Costos de transporte de plantas a Envigado
        pd.DataFrame({
            'PLANTA': Plantas,
            'C_TRANS_ENVIGADO': 4000000
        }).to_excel(writer, sheet_name='C.TTE.ENVIGADO', index=False) 

        # Hoja de ejemplo para Capacidad de planta
        pd.DataFrame({
            'PLANTA': Plantas,
            'CAP_PLANTA': 50
        }).to_excel(writer, sheet_name='CAP.PLANTA', index=False) 

        #Hoja de ejemplo para el precio por kg negociado en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'COMPRAS': 8000,
            'INTEGRACION': 8000
        }).to_excel(writer,sheet_name='PRECIO.KG',index=False)

        #Hoja de ejemplo para el promedio de peso de reses en cada zona
        pd.DataFrame({
            'ZONA': Zonas,
            'PESO': 400
        }).to_excel(writer,sheet_name='PESO.RES',index=False)
        
        # Hojas de ejemplo para Mermas (Necesarias para crear_diccionario_rdto_total)
        pd.DataFrame({
            'ZONA': [zona for zona in Zonas for _ in Plantas],
            'PLANTA': Plantas * len(Zonas),
            'MERMA': 0.02
        }).to_excel(writer, sheet_name='MERMA.TTE.ZONAPLANTA', index=False)

        pd.DataFrame({
            'PLANTA': Plantas,
            'R_CANAL_CALIENTE': 0.01,
            'M_FRIO': 0.01
        }).to_excel(writer, sheet_name='MERMA.PLANTA', index=False)
   
    
    st.download_button(
        label="Descargar plantilla",
        data=output.getvalue(),
        file_name="plantilla_sacrificio_reses.xlsx",
        mime="application/vnd.ms-excel"

    )




















