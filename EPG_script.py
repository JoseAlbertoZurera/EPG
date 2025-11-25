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
                        # ⚠️ Detectar si la hora es '24:00:00' (no válida en datetime)
                        if ts[8:14] == '240000':
                            # Convertir a '00:00:00' del día siguiente
                            base = datetime.strptime(ts[:8], '%Y%m%d') + timedelta(days=1)
                            ts = base.strftime('%Y%m%d') + '000000' + ts[14:]

                        # 🕒 Parsear fecha y hora original (incluyendo el offset)
                        dt = datetime.strptime(ts, '%Y%m%d%H%M%S %z')

                        # 🌍 Convertir a UTC
                        dt_utc = dt.astimezone(timezone.utc)

                        # ✅ Guardar en formato estándar UTC con offset '+0000'
                        elem.attrib[attr] = dt_utc.strftime('%Y%m%d%H%M%S +0000')

                    except Exception as e:
                        print(f"❌ Error procesando atributo '{attr}' con valor '{ts}': {e}")

            programs.append(ET.tostring(elem, encoding='unicode'))

    return channels, programs

# ==============================
# Función para duplicar y filtrar canales según ids.json
# ==============================
def apply_multiple_ids_filtered(channels, programs, id_map):
    """
    Duplica canales y programas según id_map y filtra solo los canales presentes en el JSON
    """
    new_channels = []
    new_programs = []

    # Duplicar y filtrar canales
    for ch in channels:
        elem = ET.fromstring(ch)
        old_id = elem.attrib.get("id")
        if old_id in id_map:
            for new_id in id_map[old_id]:
                new_elem = ET.Element(elem.tag, {"id": new_id})
                for child in elem:
                    new_elem.append(child)
                new_channels.append(ET.tostring(new_elem, encoding="unicode"))

    # Duplicar y filtrar programas
    for pr in programs:
        elem = ET.fromstring(pr)
        old_channel = elem.attrib.get("channel")
        if old_channel in id_map:
            for new_id in id_map[old_channel]:
                new_elem = ET.Element(elem.tag, elem.attrib)
                new_elem.set("channel", new_id)
                for child in elem:
                    new_elem.append(child)
                new_programs.append(ET.tostring(new_elem, encoding="unicode"))

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
    # Guardar archivo EPG normal
    # ==============================
    with open(FINAL_XML, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<tv generator-info-name="EPG {datetime.now().strftime("%d/%m/%Y %H:%M")}">\n')
        f.writelines(all_channels)
        f.writelines(all_programs)
        f.write('</tv>\n')

    # Validar XML
    try:
        ET.parse(FINAL_XML)
        print(f"\n✅ EPG generado en {FINAL_XML}")
        print(f"📺 Canales: {len(all_channels)}")
        print(f"🗓️ Programas: {len(all_programs)}")
        print("✅ Validación XML: El archivo EPG.xml está bien formado.")
    except ET.ParseError as e:
        print(f"❌ Validación XML fallida: {e}")
        log(f"[ERROR] El archivo EPG.xml no es un XML válido: {e}")

    # ==============================
    # Generar EPG8K.xml filtrado y duplicando programas según ids.json
    # ==============================
    if os.path.exists(IDS_JSON):
        with open(IDS_JSON, "r", encoding="utf-8") as jf:
            id_map = json.load(jf)

        print("\n🔄 Generando EPG8K.xml con múltiples IDs filtrados por JSON...")
        new_channels, new_programs = apply_multiple_ids_filtered(all_channels, all_programs, id_map)

        with open(EPG8K_XML, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<tv generator-info-name="EPG8K">\n')
            f.writelines(new_channels)
            f.writelines(new_programs)
            f.write('</tv>\n')

        print(f"✅ EPG8K.xml generado correctamente con múltiples IDs y solo los canales del JSON.")
    else:
        print("⚠️ No existe ids.json — no se generará EPG8K.xml")

    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
        print(f"📝 Revisa el log: {LOG_FILE}")

if __name__ == "__main__":
    main()

