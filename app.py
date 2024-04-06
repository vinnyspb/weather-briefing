import logging
import sys
from datetime import datetime
from typing import Dict

from bs4 import BeautifulSoup

import requests
from flask import Flask, render_template, request

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

app = Flask(__name__)

LOCATIONS = {
    'ESSN': {'lon': 18.697, 'lat': 59.733, 'runways': [70]},  # Norrtälje
    'ESSU': {'lon': 16.708, 'lat': 59.35, 'runways': [180]},  # Eskilstuna
    'ESSB': {'lon': 17.912, 'lat': 59.469, 'runways': [120]},  # Bromma
    'ESHR': {'lon': 18.251, 'lat': 59.48},  # Åkersberga
    'ESKT': {'lon': 17.429, 'lat': 60.347, 'runways': [160]},  # Tierp
    'ESSA': {'lon': 17.916, 'lat': 59.652, 'runways': [190, 260]},  # Arlanda
    'GIMO': {'lon': 18.097, 'lat': 60.102, 'runways': [50]},  # GIMO/Lunda
    'Frölunda': {'lon': 17.708, 'lat': 59.456, 'runways': [160]},  # Frölunda
    'ESOW': {'lon': 16.634, 'lat': 59.59, 'runways': [10]},  # Västerås
    'ESSX': {'lon': 16.502, 'lat': 59.578, 'runways': [50]},  # Johannisberg
}


def parse_html(url) -> Dict[str, str]:
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    data = {}
    for div in soup.find_all('div', {'class': ['tor-link-text-row', 'tor-link-text-row no-background']}):
        items = div.find_all('span', {'class': 'tor-link-text-row-item'})
        if len(items) == 2:
            data[items[0].text] = items[1].text

    return data


def get_metar() -> Dict[str, str]:
    try:
        url = 'https://aro.lfv.se/Links/Link/ViewLink?TorLinkId=314&type=MET'
        return parse_html(url)
    except Exception as e:
        logging.error('Failed to fetch METAR data', e)
        return {}


def get_taf() -> Dict[str, str]:
    try:
        url = 'https://aro.lfv.se/Links/Link/ViewLink?TorLinkId=315&type=MET'
        return parse_html(url)
    except Exception as e:
        logging.error('Failed to fetch METAR data', e)
        return {}


def dew_point_from_relative_humidity(temp, rel_humidity) -> float:
    return temp - ((100 - rel_humidity) / 5)


def calculate_cloud_base(temp, rel_humidity) -> int:
    diff = temp - dew_point_from_relative_humidity(temp, rel_humidity)
    return int(400 * diff)


def meters_per_second_to_knots(mps) -> int:
    return int(mps * 1.94384)


def convert_to_unix_time(time: str) -> float:
    return datetime.fromisoformat(time).timestamp()


def is_forecast_within_boundaries(forecast_time: str, from_time: str, to_time: str) -> bool:
    if not from_time and not to_time:
        return True

    forecast_unix = convert_to_unix_time(forecast_time)
    if from_time:
        from_unix = convert_to_unix_time(from_time)
        if forecast_unix < from_unix:
            return False
    if to_time:
        to_unix = convert_to_unix_time(to_time)
        if forecast_unix > to_unix:
            return False
    return True


def clouds_coverage(reported: int) -> str:
    if reported == 0:
        return "NC"
    elif reported <= 2:
        return "FEW"
    elif reported <= 4:
        return "SCT"
    elif reported <= 7:
        return "BKN"
    else:
        return "OVC"


def precipitation_category(pcat: int) -> str:
    if pcat == 0:
        return "None"
    elif pcat == 1:
        return "Snow"
    elif pcat == 2:
        return "Snow and rain"
    elif pcat == 3:
        return "Rain"
    elif pcat == 4:
        return "Drizzle"
    elif pcat == 5:
        return "Freezing rain"
    elif pcat == 6:
        return "Freezing drizzle"
    else:
        return "Unknown"


@app.route('/')
def index():
    from_time = request.args.get('from')
    to_time = request.args.get('to')
    locations_param = request.args.get('locations')

    forecasts = {}

    metar = get_metar()
    taf = get_taf()

    locations = locations_param.split(',') if locations_param else ['Norrtälje']
    for location in locations:
        if location in LOCATIONS:
            coords = LOCATIONS[location]
            api_url = f"https://opendata-download-metfcst.smhi.se/api/category/pmp3g/version/2/geotype/point/lon/{coords['lon']}/lat/{coords['lat']}/data.json"

            response = requests.get(api_url, headers={'Accept': 'application/json',
                                                      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'})
            if response.status_code == 200:
                data = response.json()
                forecast_data = []
                for entry in data['timeSeries']:
                    if not is_forecast_within_boundaries(entry['validTime'], from_time, to_time):
                        continue

                    forecast = {}
                    parameters = {param['name']: param['values'][0] for param in entry['parameters']}
                    forecast['validTime'] = entry['validTime']
                    forecast['temperature'] = parameters.get('t')
                    forecast['humidity'] = parameters.get('r')
                    forecast['qnh'] = round(parameters.get('msl'))
                    forecast['clouds'] = clouds_coverage(parameters.get('lcc_mean'))
                    forecast['wind_speed_knots'] = meters_per_second_to_knots(parameters.get('ws'))
                    forecast['wind_gust_knots'] = meters_per_second_to_knots(parameters.get('gust'))
                    forecast['wind_direction'] = parameters.get('wd')
                    forecast['visibility_meters'] = int(parameters.get('vis') * 1000)  # Convert km to meters
                    forecast['dewPoint'] = round(dew_point_from_relative_humidity(forecast['temperature'],
                                                                                  forecast['humidity']), 1)
                    forecast['cloudBase'] = calculate_cloud_base(forecast['temperature'], forecast['humidity'])
                    forecast['pcat'] = precipitation_category(int(parameters.get('pcat')))
                    forecast['pmin'] = float(parameters.get('pmin'))
                    forecast['pmax'] = float(parameters.get('pmax'))
                    forecast['pmean'] = float(parameters.get('pmean'))
                    forecast['pmedian'] = float(parameters.get('pmedian'))
                    forecast['spp'] = int(parameters.get('spp'))
                    forecast['runways'] = coords['runways'] if 'runways' in coords else []

                    forecast_data.append(forecast)
                forecasts[location] = forecast_data
            else:
                continue

    return render_template('forecast.html', forecast_data=forecasts, metar=metar, taf=taf)


if __name__ == '__main__':
    app.run(debug=True, port=5555)
