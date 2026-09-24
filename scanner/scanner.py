import serial


def main():
    port = "/dev/ttyAMA0" # Filler for now, will be the barcode scanner when the time comes
    scanner = serial.Serial(port=port, baudrate=9600, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=1)

    while True:
        data = serial.read_until(b"\r")
        if data:
            print(data.decode(errors="replace").strip(), flush=True)


