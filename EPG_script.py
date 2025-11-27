import os
import gzip
import shutil
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import time
import sys
import json

# Forzar zona horaria a España (funciona en Linux/GitHub Actions)
if sys.platform != 'win32':
    os.environ['TZ'] = 'Europe/Madrid'
    time.tzset()  # Solo en Unix
else:
    print("tzset no disponible en Windows. Usa pytz/zoneinfo.")

INPUT_FILE = 'urls.txt'
FINAL_XML = 'EPG.xml'
EPG8K_XML = 'EPG8K.xml'
LOG_FILE = 'EPG.log'
IDS_JSON = 'ids.json'

def log(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a', encoding='utf-8') as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

def download_file(url, dest_filename):
    print(f"Descargando: {url}")
    with urllib.request.urlopen(url) as response:
        with open(dest_filename, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

def decompress_gz(src, dest):
    print(f"Descomprimiendo archivo: {src}")
    with gzip.open(src, 'rb') as f_in:
        with open(dest, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

def extract_elements(xml_file):
    channels, programs = [], []

    tree = ET.parse(xml_file)
    for elem in tree.getroot():
        if elem.tag == 'channel':
            channels.append(ET.tostring(elem, encoding='unicode'))

        elif elem.tag == 'programme':
            for attr in ('start', 'stop'):
                if attr in elem.attrib:
                    ts = elem.attrib[attr]

                    try:
                        if ts[8:14] == '240000':
                            base = datetime.strptime(ts[:8], '%Y%m%d') + timedelta(days=1)
                            ts = base.strftime('%Y%m%d') + '000000' + ts[14:]

                        dt = datetime.strptime(ts, '%Y%m%d%H%M%S %z')
                        dt_utc = dt.astimezone(timezone.utc)
                        elem.attrib[attr] = dt_utc.strftime('%Y%m%d%H%M%S +0000')

                    except Exception as e:
                        print(f"❌ Error procesando atributo '{attr}' con valor '{ts}': {e}")

            programs.append(ET.tostring(elem, encoding='unicode'))

    return channels, programs

# ======================================================
# NUEVA función: cambiar display-name y duplicar canales
# ======================================================
def process_channels_and_programs(channels, programs, id_map):
    """
    Procesa canales y programas:
    - filtra por los canales presentes en id_map
    - para cada canal, reemplaza/añade display-name(s) según id_map (acepta string o lista)
    - clona el canal para cada id adicional
    - clona los programas para cada id (incluido el original)
    """
    new_channels = []
    new_programs = []

    for ch in channels:
        elem = ET.fromstring(ch)
        original_id = elem.attrib.get("id")
        if original_id is None:
            continue

        if original_id not in id_map:
            continue  # ignorar canales no listados en ids.json

        conf = id_map[original_id]

        # obtener lista de ids extra (asegurarse que sea lista)
        extra_ids = conf.get("ids", [])
        if not isinstance(extra_ids, list):
            extra_ids = [extra_ids]

        # obtener display-name(s) — puede ser string o lista
        disp = conf.get("display-name")
        display_names = []
        if disp is None:
            display_names = None  # señal: mantener los display-name originales
        elif isinstance(disp, list):
            display_names = [str(x) for x in disp]
        else:
            display_names = [str(disp)]

        # Crear una copia del elemento canal para modificar display-name(s)
        base_elem = ET.fromstring(ET.tostring(elem, encoding='unicode'))

        if display_names is not None:
            # eliminar display-name(s) existentes
            for dn in base_elem.findall("display-name"):
                base_elem.remove(dn)
            # añadir los display-name solicitados (en el orden de la lista)
            for idx, dn_text in enumerate(display_names):
                new_dn = ET.Element("display-name")
                new_dn.text = dn_text
                # insertamos en el inicio para mantener consistencia
                base_elem.insert(idx, new_dn)

        # añadir canal principal (con su id original)
        new_channels.append(ET.tostring(base_elem, encoding='unicode'))

        # clonar canal para cada id extra
        for new_id in extra_ids:
            clone = ET.fromstring(ET.tostring(base_elem, encoding='unicode'))
            clone.attrib["id"] = str(new_id)
            new_channels.append(ET.tostring(clone, encoding='unicode'))

    # PROGRAMAS: duplicar solo los de los canales listados
    for pr in programs:
        elem = ET.fromstring(pr)
        original_channel = elem.attrib.get("channel")
        if original_channel is None:
            continue
        if original_channel not in id_map:
            continue

        conf = id_map[original_channel]
        extra_ids = conf.get("ids", [])
        if not isinstance(extra_ids, list):
            extra_ids = [extra_ids]

        # todos los ids para los que crearemos programas: original + extras
        all_ids = [original_channel] + [str(i) for i in extra_ids]

        for new_id in all_ids:
            clone = ET.fromstring(pr)
            clone.attrib["channel"] = str(new_id)
            new_programs.append(ET.tostring(clone, encoding='unicode'))

    return new_channels, new_programs

def main():
    all_channels = []
    all_programs = []

    if not os.path.exists(INPUT_FILE):
        log(f"[ERROR] No se encontró el archivo {INPUT_FILE}")
        print(f"❌ No se encontró el archivo {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r', encoding='utf-8') as file:
        urls = [line.strip() for line in file if line.strip()]

    for i, url in enumerate(urls):
        base_filename = f'temp_{i}.xml'
        try:
            if url.endswith('.gz'):
                gz_filename = base_filename + '.gz'
                download_file(url, gz_filename)
                decompress_gz(gz_filename, base_filename)
                os.remove(gz_filename)
            else:
                download_file(url, base_filename)

            ch, pr = extract_elements(base_filename)
            all_channels.extend(ch)
            all_programs.extend(pr)

            print(f"✅ EPG procesado correctamente: {url} (Canales: {len(ch)}, Programas: {len(pr)})")

        except Exception as e:
            log(f"[ERROR] Error procesando {url}: {e}")
            print(f"❌ Error procesando {url}: {e}")
        finally:
            if os.path.exists(base_filename):
                os.remove(base_filename)

    # ==============================
    # Guardar EPG normal
    # ==============================
    with open(FINAL_XML, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<tv generator-info-name="EPG {datetime.now().strftime("%d/%m/%Y %H:%M")}">\n')
        f.writelines(all_channels)
        f.writelines(all_programs)
        f.write('</tv>\n')

    try:
        ET.parse(FINAL_XML)
        print(f"\n✅ EPG generado en {FINAL_XML}")
    except ET.ParseError as e:
        print(f"❌ Error en EPG normal: {e}")

    # ==========================================
    # Generar EPG8K con ids.json + display-names
    # ==========================================
    if os.path.exists(IDS_JSON):
        with open(IDS_JSON, "r", encoding="utf-8") as jf:
            id_map = json.load(jf)

        print("\n🔄 Generando EPG8K.xml con IDs múltiples + display-name personalizado...")

        new_channels, new_programs = process_channels_and_programs(all_channels, all_programs, id_map)

        with open(EPG8K_XML, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<tv generator-info-name="EPG8K">\n')
            f.writelines(new_channels)
            f.writelines(new_programs)
            f.write('</tv>\n')

        print(f"✅ EPG8K.xml generado correctamente.")

    else:
        print("⚠️ No existe ids.json — no se generará EPG8K.xml")

    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
        print(f"📝 Revisa el log: {LOG_FILE}")

if __name__ == "__main__":
    main()


