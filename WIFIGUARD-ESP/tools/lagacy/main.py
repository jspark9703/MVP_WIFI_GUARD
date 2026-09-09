#!/usr/bin/env python3
# -*-coding:utf-8-*-

# SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#

# WARNING: we don't check for Python build-time dependencies until
# check_environment() function below. If possible, avoid importing
# any external libraries here - put in external script, or import in
# their specific function instead.

import sys
import csv
import json
import argparse
import os
import pandas as pd
import numpy as np
from datetime import datetime

import serial
from os import path
from io import StringIO

from PyQt5.Qt import *
from pyqtgraph import PlotWidget
from PyQt5 import QtCore
import pyqtgraph as pg
from pyqtgraph import ScatterPlotItem
from PyQt5.QtCore import pyqtSignal, QThread
import threading
import queue
import time
import struct
from scipy.optimize import minimize
import matplotlib.pyplot as plt
from scipy.stats import linregress
import statsmodels.api as sm

# Reduce displayed waveforms to avoid display freezes
CSI_VAID_SUBCARRIER_INTERVAL = 1
csi_vaid_subcarrier_len =0

CSI_DATA_INDEX = 200  # buffer size
CSI_DATA_COLUMNS = 490
DATA_COLUMNS_NAMES_C5C6 = ['type', 'id', 'mac', 'rssi', 'rate','noise_floor','fft_gain','agc_gain', 'channel', 'local_timestamp',  'sig_len', 'rx_state', 'len', 'first_word', 'data']
DATA_COLUMNS_NAMES_NEW = ['type', 'mac','len', 'first_word', 'data']
DATA_COLUMNS_NAMES = ['type', 'id', 'mac', 'rssi', 'rate', 'sig_mode', 'mcs', 'bandwidth', 'smoothing', 'not_sounding', 'aggregation', 'stbc', 'fec_coding',
                      'sgi', 'noise_floor', 'ampdu_cnt', 'channel', 'secondary_channel', 'local_timestamp', 'ant', 'sig_len', 'rx_state', 'len', 'first_word', 'data']

csi_data_array = np.zeros(
    [CSI_DATA_INDEX, CSI_DATA_COLUMNS], dtype=np.float64)
csi_data_phase = np.zeros([CSI_DATA_INDEX, CSI_DATA_COLUMNS], dtype=np.float64)
csi_data_complex = np.zeros([CSI_DATA_INDEX, CSI_DATA_COLUMNS], dtype=np.complex64)
agc_gain_data = np.zeros([CSI_DATA_INDEX], dtype=np.float64)
fft_gain_data = np.zeros([CSI_DATA_INDEX], dtype=np.float64)
fft_gains = []
agc_gains = []

# RAW_DATA 独立数据数组
RAW_DATA_COLUMNS = 612  # RAW_DATA 最大长度
raw_data_complex = np.zeros([CSI_DATA_INDEX, RAW_DATA_COLUMNS], dtype=np.complex64)
SERIAL_QUEUE_MAXSIZE = 2048
QUEUE_READ_TIMEOUT_SEC = 0.2
LOG_FLUSH_INTERVAL_SEC = 0.5
BINARY_FRAME_MAGIC = 0xA55A
BINARY_FRAME_VERSION = 1
BINARY_FRAME_TYPE_CSI = 1
BINARY_FRAME_HEADER_FORMAT = '<HHBBI6sbBBBBBBBBBBbBBBBHHIbBHBB'
BINARY_FRAME_HEADER_SIZE = struct.calcsize(BINARY_FRAME_HEADER_FORMAT)
BINARY_FRAME_CHECKSUM_SIZE = 2
BINARY_FRAME_MAX_SIZE = BINARY_FRAME_HEADER_SIZE + RAW_DATA_COLUMNS + BINARY_FRAME_CHECKSUM_SIZE
BINARY_MAGIC_BYTES = struct.pack('<H', BINARY_FRAME_MAGIC)

class csi_data_graphical_window(QWidget):
    def __init__(self):
        super().__init__()

        self.resize(1280, 900)

        self.plotWidget_ted = PlotWidget(self)
        self.plotWidget_ted.setGeometry(QtCore.QRect(0, 0, 640, 300))
        self.plotWidget_ted.setYRange(-2*np.pi, 2*np.pi)
        self.plotWidget_ted.addLegend()
        self.plotWidget_ted.setTitle('Phase Data - Last Frame')  # 添加标题
        self.plotWidget_ted.setLabel('left', 'Phase (rad)')  # Y轴标签
        self.plotWidget_ted.setLabel('bottom', 'Subcarrier Index')  # X轴标签

        self.csi_amplitude_array = np.abs(csi_data_complex)
        self.csi_phase_array = np.angle(csi_data_complex)
        self.curve = self.plotWidget_ted.plot([], name='CSI Phase', pen='r')

        # RAW_DATA 相位曲线（蓝色）
        self.raw_amplitude_array = np.abs(raw_data_complex)
        self.raw_phase_array = np.angle(raw_data_complex)
        self.curve_raw = self.plotWidget_ted.plot([], name='RAW Phase', pen='b')

        self.plotWidget_multi_data = PlotWidget(self)
        self.plotWidget_multi_data.setGeometry(QtCore.QRect(0, 300, 1280, 300))
        self.plotWidget_multi_data.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plotWidget_multi_data.addLegend()
        self.plotWidget_multi_data.setTitle('Subcarrier Amplitude Data')  # 添加标题
        self.plotWidget_multi_data.setLabel('left', 'Amplitude')  # Y轴标签
        self.plotWidget_multi_data.setLabel('bottom', 'Time (Cumulative Packet Count)')  # X轴标签

        self.curve_list = []
        agc_curve = self.plotWidget_multi_data.plot(
            agc_gain_data, name='AGC Gain', pen=[255,255,0])
        fft_curve = self.plotWidget_multi_data.plot(
            fft_gain_data, name='FFT Gain', pen=[255,255,0])
        self.curve_list.append(agc_curve)
        self.curve_list.append(fft_curve)

        for i in range(CSI_DATA_COLUMNS):
            curve = self.plotWidget_multi_data.plot(
                self.csi_amplitude_array[:, i], name=str(i), pen=(255, 255, 255))
            self.curve_list.append(curve)



        self.plotWidget_phase_data = PlotWidget(self)
        self.plotWidget_phase_data.setGeometry(QtCore.QRect(0, 600, 1280, 300))
        self.plotWidget_phase_data.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plotWidget_phase_data.addLegend()
        self.plotWidget_multi_data.setTitle('Subcarrier Phase Data')  # 添加标题
        self.plotWidget_multi_data.setLabel('left', 'Phase (rad)')  # Y轴标签
        self.plotWidget_multi_data.setLabel('bottom', 'Time (Cumulative Packet Count)')  # X轴标签


        self.curve_phase_list = []
        for i in range(CSI_DATA_COLUMNS):
            phase_curve = self.plotWidget_phase_data.plot(
                np.angle(self.csi_amplitude_array[:, i]), name=str(i), pen=(255, 255, 255))
            self.curve_phase_list.append(phase_curve)


        # IQ 图窗口
        self.plotWidget_iq = PlotWidget(self)
        self.plotWidget_iq.setGeometry(QtCore.QRect(640, 0, 640, 300))
        self.plotWidget_iq.setLabel('left', 'Q (Imag)')
        self.plotWidget_iq.setLabel('bottom', 'I (Real)')
        self.plotWidget_iq.setTitle('IQ Plot - Last Frame')
        view_box = self.plotWidget_iq.getViewBox()
        view_box.setRange(QtCore.QRectF(-30, -30, 60, 60))  # 可以调整范围的大小，保证原点在中间

        self.plotWidget_iq.getViewBox().setAspectLocked(True)
        self.iq_scatter = ScatterPlotItem(size=6)
        self.plotWidget_iq.addItem(self.iq_scatter)

        self.iq_colors = []



        self.timer = pg.QtCore.QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(100)
        self.deta_len = 0

    def update_curve_colors(self, color_list):
        self.deta_len = len(color_list)
        self.iq_colors = color_list
        self.plotWidget_ted.setXRange(0, self.deta_len//2)
        for i in range(self.deta_len):
            self.curve_list[i].setPen(color_list[i])
            self.curve_phase_list[i].setPen(color_list[i])

    def update_data(self):

        i = np.real(csi_data_complex[-1, :])
        q = np.imag(csi_data_complex[-1, :])

        # points = []
        # for idx in range(self.deta_len):
        #     if idx < len(self.iq_colors):
        #         color = self.iq_colors[idx]
        #     else:
        #         color = (200, 200, 200)
        #     points.append({'pos': (i[idx], q[idx]), 'brush': pg.mkBrush(color)})

        # self.iq_scatter.setData(points)

        # CSI_DATA 相位更新
        self.csi_amplitude_array = np.abs(csi_data_complex)
        self.csi_phase_array = np.angle(csi_data_complex)
        self.csi_row_data = self.csi_phase_array[-1, :]

        self.csi_row_data = np.unwrap(self.csi_row_data)
        self.curve.setData(self.csi_row_data)

        # RAW_DATA 相位更新
        self.raw_amplitude_array = np.abs(raw_data_complex)
        self.raw_phase_array = np.angle(raw_data_complex)
        self.raw_row_data = self.raw_phase_array[-1, :]

        self.raw_row_data = np.unwrap(self.raw_row_data)
        self.curve_raw.setData(self.raw_row_data)

        # self.curve_list[CSI_DATA_COLUMNS].setData(agc_gain_data)
        # self.curve_list[CSI_DATA_COLUMNS+1].setData(fft_gain_data)

        for i in range(CSI_DATA_COLUMNS):
            self.curve_list[i].setData(self.csi_amplitude_array[:, i])
            # self.curve_phase_list[i].setData(self.csi_phase_array[:, i])

def generate_subcarrier_colors(red_range, green_range, yellow_range, total_num,interval=1):
    colors = []
    for i in range(total_num):
        if red_range and red_range[0] <= i <= red_range[1]:
            intensity = int(255 * (i - red_range[0]) / (red_range[1] - red_range[0]))
            colors.append((intensity, 0, 0))
        elif green_range and green_range[0] <= i <= green_range[1]:
            intensity = int(255 * (i - green_range[0]) / (green_range[1] - green_range[0]))
            colors.append((0, intensity, 0))
        elif yellow_range and yellow_range[0] <= i <= yellow_range[1]:
            intensity = int(255 * (i - yellow_range[0]) / (yellow_range[1] - yellow_range[0]))
            colors.append((0, intensity, intensity))
        else:
            colors.append((200, 200, 200))

    return colors


def _build_csi_csv_row_from_binary(header_values, csi_raw_data):
    (
        _magic, _frame_len, _version, _frame_type, seq, mac_bytes, rssi, rate,
        sig_mode, mcs, bandwidth, smoothing, not_sounding, aggregation, stbc,
        fec_coding, sgi, noise_floor, ampdu_cnt, channel, secondary_channel,
        ant, sig_len, rx_state, local_timestamp, _fft_gain, _agc_gain, csi_len,
        first_word_invalid, _reserved
    ) = header_values

    mac = ':'.join(f'{b:02x}' for b in mac_bytes)
    return [
        'CSI_DATA', seq, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing,
        not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor,
        ampdu_cnt, channel, secondary_channel, local_timestamp, ant, sig_len,
        rx_state, csi_len, first_word_invalid, json.dumps(csi_raw_data)
    ]


def _process_csi_packet(csi_data, csi_raw_data, csv_writer, callback, state):
    global fft_gains, agc_gains

    csi_data_len = int(csi_data[-3])
    fft_gain = 0
    agc_gain = 0

    fft_gains.append(fft_gain)
    agc_gains.append(agc_gain)
    csv_writer.writerow(csi_data)

    csi_data_complex[:-1] = csi_data_complex[1:]
    agc_gain_data[:-1] = agc_gain_data[1:]
    fft_gain_data[:-1] = fft_gain_data[1:]
    agc_gain_data[-1] = agc_gain
    fft_gain_data[-1] = fft_gain

    if state['csi_count'] == 0:
        state['csi_count'] = 1
        print('CSI_DATA detected, length:', csi_data_len)
        if csi_data_len == 106:
            colors = generate_subcarrier_colors((0,25), (27,53), None, len(csi_raw_data))
        elif  csi_data_len == 114:
            colors = generate_subcarrier_colors((0,27), (29,56), None, len(csi_raw_data))
        elif  csi_data_len == 52:
            colors = generate_subcarrier_colors((0,12), (13,26), None, len(csi_raw_data))
        elif  csi_data_len == 234:
            colors = generate_subcarrier_colors((0,28), (29,56), (60,116), len(csi_raw_data))
        elif  csi_data_len == 228:
            colors = generate_subcarrier_colors((0,28), (29,56), (57,114), len(csi_raw_data))
        elif  csi_data_len == 328:
            colors = generate_subcarrier_colors((0,164), None, None, len(csi_raw_data))
        elif  csi_data_len == 490:
            colors = generate_subcarrier_colors((0,61), (62,122), (123,245), len(csi_raw_data))
        elif  csi_data_len == 128:
            colors = generate_subcarrier_colors((0,31), (32,63), None, len(csi_raw_data))
        elif  csi_data_len == 256:
            colors = generate_subcarrier_colors((0,32), (32,63), (64,128), len(csi_raw_data))
        elif  csi_data_len == 512:
            colors = generate_subcarrier_colors((0,63), (64,127), (128,256), len(csi_raw_data))
        elif  csi_data_len == 384:
            colors = generate_subcarrier_colors((0,63), (64,127), (128,192), len(csi_raw_data))
        else:
            print('Please add more color schemes.')
            state['csi_count'] = 0
            return
        callback(colors)

    for i in range(csi_data_len // 2):
        csi_data_complex[-1][i] = complex(csi_raw_data[i * 2 + 1], csi_raw_data[i * 2])


def _extract_binary_frames(buffer, log_file_fd, state):
    frames = []
    while True:
        start = buffer.find(BINARY_MAGIC_BYTES)
        if start < 0:
            if len(buffer) > BINARY_FRAME_HEADER_SIZE:
                del buffer[:-BINARY_FRAME_HEADER_SIZE]
            break
        if start > 0:
            junk = bytes(buffer[:start]).decode('utf-8', errors='ignore').strip()
            if junk:
                log_file_fd.write(junk + '\n')
            del buffer[:start]

        if len(buffer) < BINARY_FRAME_HEADER_SIZE:
            break

        frame_len = struct.unpack_from('<H', buffer, 2)[0]
        if frame_len < (BINARY_FRAME_HEADER_SIZE + BINARY_FRAME_CHECKSUM_SIZE) or frame_len > BINARY_FRAME_MAX_SIZE:
            del buffer[:2]
            continue
        if len(buffer) < frame_len:
            break

        frame = bytes(buffer[:frame_len])
        del buffer[:frame_len]
        frames.append(frame)

    now = time.monotonic()
    if now - state['last_log_flush'] >= LOG_FLUSH_INTERVAL_SEC:
        log_file_fd.flush()
        state['last_log_flush'] = now
    return frames


def csi_data_read_parse(port: str, csv_writer, log_file_fd, callback=None, duration_sec=None):
    serial_port = serial.Serial(
        port=port, baudrate=921600, bytesize=8, parity='N', stopbits=1, timeout=0.1
    )
    if serial_port.isOpen():
        print('open success')
    else:
        print('open failed')
        return

    byte_queue = queue.Queue(maxsize=SERIAL_QUEUE_MAXSIZE)
    stop_event = threading.Event()
    state = {
        'csi_count': 0,
        'last_log_flush': time.monotonic(),
        'dropped_chunks': 0,
        'bad_frames': 0,
    }
    serial_buffer = bytearray()

    def serial_reader():
        while not stop_event.is_set():
            try:
                waiting = serial_port.in_waiting
                data = serial_port.read(waiting if waiting > 0 else 1)
            except serial.SerialException as e:
                print(f'serial read error: {e}')
                break

            if not data:
                continue

            try:
                byte_queue.put_nowait(data)
            except queue.Full:
                # Keep latency low by dropping oldest chunks when parser is slower than input.
                try:
                    byte_queue.get_nowait()
                    state['dropped_chunks'] += 1
                except queue.Empty:
                    pass
                try:
                    byte_queue.put_nowait(data)
                except queue.Full:
                    pass

    reader_thread = threading.Thread(target=serial_reader, daemon=True)
    reader_thread.start()

    start_time = time.monotonic()

    try:
        while True:
            if duration_sec is not None and (time.monotonic() - start_time) >= duration_sec:
                print(f'stop capture after {duration_sec} seconds')
                break

            try:
                chunk = byte_queue.get(timeout=QUEUE_READ_TIMEOUT_SEC)
            except queue.Empty:
                if not reader_thread.is_alive():
                    break
                continue

            serial_buffer.extend(chunk)
            frames = _extract_binary_frames(serial_buffer, log_file_fd, state)
            for frame in frames:
                header_values = struct.unpack_from(BINARY_FRAME_HEADER_FORMAT, frame, 0)
                frame_len = header_values[1]
                version = header_values[2]
                frame_type = header_values[3]
                csi_len = header_values[27]

                if version != BINARY_FRAME_VERSION or frame_type != BINARY_FRAME_TYPE_CSI:
                    state['bad_frames'] += 1
                    continue
                if csi_len + BINARY_FRAME_HEADER_SIZE + BINARY_FRAME_CHECKSUM_SIZE != frame_len:
                    state['bad_frames'] += 1
                    continue

                checksum_expected = struct.unpack_from('<H', frame, frame_len - BINARY_FRAME_CHECKSUM_SIZE)[0]
                checksum_actual = sum(frame[:-BINARY_FRAME_CHECKSUM_SIZE]) & 0xFFFF
                if checksum_actual != checksum_expected:
                    state['bad_frames'] += 1
                    continue

                payload = frame[BINARY_FRAME_HEADER_SIZE: BINARY_FRAME_HEADER_SIZE + csi_len]
                csi_raw_data = list(struct.unpack(f'<{csi_len}b', payload))
                csi_data = _build_csi_csv_row_from_binary(header_values, csi_raw_data)
                _process_csi_packet(csi_data, csi_raw_data, csv_writer, callback, state)
    finally:
        stop_event.set()
        reader_thread.join(timeout=1.0)
        if serial_port.isOpen():
            serial_port.close()
        log_file_fd.flush()
        if state['dropped_chunks'] > 0:
            print(f'dropped serial chunks to keep realtime: {state["dropped_chunks"]}')
        if state['bad_frames'] > 0:
            print(f'ignored invalid binary frames: {state["bad_frames"]}')
    return


class SubThread (QThread):
    data_ready = pyqtSignal(object)
    def __init__(self, serial_port, save_file_name, log_file_name, duration_sec=None):
        super().__init__()
        self.serial_port = serial_port
        self.duration_sec = duration_sec

        save_file_fd = open(save_file_name, 'w')
        self.save_file_fd = save_file_fd
        self.log_file_fd = open(log_file_name, 'w')
        self.csv_writer = csv.writer(save_file_fd)
        self.csv_writer.writerow(DATA_COLUMNS_NAMES)

    def run(self):
        try:
            csi_data_read_parse(
                self.serial_port,
                self.csv_writer,
                self.log_file_fd,
                callback=self.data_ready.emit,
                duration_sec=self.duration_sec,
            )
        finally:
            try:
                self.save_file_fd.flush()
                self.save_file_fd.close()
            except Exception:
                pass
            try:
                self.log_file_fd.flush()
                self.log_file_fd.close()
            except Exception:
                pass


def _build_default_csv_path(suffix=None):
    """기본 저장: 현재 작업 디렉터리 기준 ./data/{yymmdd}/{hhmmss}[_{suffix}].csv"""
    now = datetime.now()
    date_str = now.strftime('%y%m%d')
    time_str = now.strftime('%H%M%S')
    base_name = time_str
    if suffix:
        base_name = f'{base_name}_{suffix}'
    # Windows에서 path.join(path.sep, 'data', ...) 는 C:\data\... 가 되어 프로젝트 폴더에 안 보임
    return path.join(os.getcwd(), 'data', date_str, f'{base_name}.csv')


if __name__ == '__main__':
    if sys.version_info < (3, 6):
        print(' Python version should >= 3.6')
        exit()

    parser = argparse.ArgumentParser(
        description='Read CSI data from serial port and display it graphically')
    parser.add_argument('-p', '--port', dest='port', action='store', required=True,
                        help='Serial port number of csv_recv device')
    parser.add_argument('-s', '--store', dest='store_file', action='store', default=None,
                        help='CSV output file path; if omitted, use ./data/{yymmdd}/{hhmmss}[_{suffix}].csv (cwd 기준)')
    parser.add_argument('--suffix', dest='suffix', action='store', default=None,
                        help='Optional suffix for auto-generated CSV: ./data/{yymmdd}/{hhmmss}_{suffix}.csv')
    parser.add_argument('-t', '--time', dest='duration_sec', action='store', type=int, default=None,
                        help='Capture duration in seconds; e.g. 60 means stop after 60 seconds')
    parser.add_argument('-l', '--log', dest='log_file', action='store', default='./csi_data_log.txt',
                        help='Save other serial data the bad CSI data to a log file')

    args = parser.parse_args()
    serial_port = args.port
    file_name = args.store_file if args.store_file else _build_default_csv_path(args.suffix)
    log_file_name = args.log_file
    duration_sec = args.duration_sec
    if duration_sec is not None and duration_sec <= 0:
        parser.error('--time must be a positive integer (seconds)')

    file_dir = path.dirname(file_name)
    if file_dir:
        os.makedirs(file_dir, exist_ok=True)
    log_dir = path.dirname(log_file_name)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    print('CSV save path:', path.abspath(file_name))
    print('Log save path:', path.abspath(log_file_name))

    app = QApplication(sys.argv)

    subthread = SubThread(serial_port, file_name, log_file_name, duration_sec=duration_sec)

    window = csi_data_graphical_window()
    subthread.data_ready.connect(window.update_curve_colors)
    if duration_sec is not None:
        subthread.finished.connect(app.quit)
    subthread.start()
    window.show()

    sys.exit(app.exec())
