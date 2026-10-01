#!/usr/bin/env python3
"""Add/update only Kári weather panels in an existing Grafana dashboard."""
import argparse
import json
from pathlib import Path

PREFIX = 'kari_weather_pilot_'
OWNER = 'kari-weather-pilot'


def update(dashboard):
    panels = [p for p in dashboard['panels'] if p.get('description', '').split('\n')[0] != OWNER]
    y = max((p['gridPos']['y'] + p['gridPos']['h'] for p in panels), default=0)
    panel_id = max((p['id'] for p in panels), default=0)

    def add(title, kind, x, row, w, h, **extra):
        nonlocal panel_id
        panel_id += 1
        panel = dict(id=panel_id, title=title, type=kind, description=OWNER,
                     gridPos=dict(x=x, y=y+row, w=w, h=h), **extra)
        panels.append(panel)
        return panel

    def stat(title, expr, x, row, unit='none', mappings=None, description=''):
        panel = add(title, 'stat', x, row, 6, 5,
            datasource=dict(type='prometheus', uid='hermodr-prometheus'),
            targets=[dict(expr=expr, refId='A', instant=True)],
            options=dict(colorMode='value', graphMode='none', reduceOptions=dict(calcs=['lastNotNull'], values=False)),
            fieldConfig=dict(defaults=dict(unit=unit, noValue='Unavailable', mappings=mappings or [], color=dict(mode='fixed', fixedColor='blue')), overrides=[]))
        panel['description'] += '\n' + description
        return panel

    def mapping(values):
        return [dict(type='value', options={str(i): dict(text=text) for i, text in values.items()})]

    add('Fixed HOME weather · seven-day pilot', 'row', 0, 0, 24, 1, collapsed=False, panels=[])
    add('Weather pilot and host placement', 'text', 0, 1, 24, 5, options=dict(mode='markdown', content='''**Weather runs on Odin. Bluetooth/BLE runs on Mimir only** because Odin’s Bluetooth hardware is unsuitable.

Fixed **HOME** configuration approved and stored privately. Open-Meteo supplies modeled estimates every **15 minutes**, not measurements at HOME. Reports update every 15 minutes, with daily verified backups. Collection stops automatically after seven days; the final assessment follows within 15 minutes. Publishing remains disabled.

Dates below use your browser’s timezone. Coverage is represented time, not sensor accuracy; solar intervals look backward and may initially show lower coverage. A running pilot has **not yet passed** its seven-day assessment. No address or coordinates are exposed here.

[Weather data: Open-Meteo](https://open-meteo.com/) · CC BY 4.0. Processed by Kári.'''))
    stat('Pilot state', PREFIX+'phase', 0, 6, mappings=mapping({0:'Awaiting HOME setup',1:'Running',2:'Completed'}))
    stat('Seven-day assessment', PREFIX+'assessment', 6, 6, mappings=mapping({0:'Not started',1:'In progress',2:'Passed',3:'Needs review'}))
    health = PREFIX+'report_available * ((('+PREFIX+'phase == bool 2) + (time() - '+PREFIX+'report_timestamp_seconds < bool 1200)) > bool 0)'
    p = stat('Reporting healthy', health+' or '+PREFIX+'report_available', 12, 6, mappings=mapping({0:'Unavailable or stale',1:'Current'}), description='Current means a readable report no older than 20 minutes, or a completed final report. Check this before relying on pilot state.')
    p['fieldConfig']['defaults']['color'] = dict(mode='thresholds')
    p['fieldConfig']['defaults']['thresholds'] = dict(mode='absolute', steps=[dict(color='red', value=None),dict(color='green',value=1)])
    stat('Backup independently verified', PREFIX+'backup_verified', 18, 6, mappings=mapping({0:'Not verified',1:'Verified'}))
    stat('Started', PREFIX+'started_timestamp_seconds * 1000', 0, 11, 'dateTimeAsLocal')
    stat('Automatic stop', PREFIX+'ends_timestamp_seconds * 1000', 6, 11, 'dateTimeAsLocal')
    stat('Time remaining', 'clamp_min('+PREFIX+'ends_timestamp_seconds - time(), 0)', 12, 11, 's')
    stat('Last report', PREFIX+'report_timestamp_seconds * 1000', 18, 11, 'dateTimeAsLocal')
    stat('Successful requests', PREFIX+'polls_successful', 0, 16)
    stat('Attempted requests', PREFIX+'polls_attempted', 6, 16)
    for title, suffix, x, unit in [('Coverage by metric','coverage_fraction',0,'percentunit'),('Samples by metric','samples',12,'short')]:
        add(title,'bargauge',x,21,12,8,
            datasource=dict(type='prometheus',uid='hermodr-prometheus'),
            targets=[dict(expr=PREFIX+suffix,refId='A',instant=True,legendFormat='{{metric}}')],
            options=dict(orientation='horizontal',displayMode='basic',reduceOptions=dict(calcs=['lastNotNull'],values=False)),
            fieldConfig=dict(defaults=dict(unit=unit,min=0,**({'max':1} if suffix=='coverage_fraction' else {})),overrides=[]))
    stat('Stale samples', 'sum('+PREFIX+'stale)',12,16)
    stat('Suspect samples', 'sum('+PREFIX+'suspect)',18,16)
    dashboard['panels'] = panels
    return dashboard


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(update(json.loads(args.source.read_text())),indent=2)+'\n')
