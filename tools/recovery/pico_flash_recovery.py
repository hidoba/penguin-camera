"""MicroPython recovery driver; loaded into RAM, not installed on the Pico.

Only 4-KiB sector erase and 256-byte program are supported. Never unlock status
registers or chip-erase. An error ends the command session without retrying.
"""


class Flash:
    def __init__(self, spi, cs, ticks_ms, ticks_diff, sleep_ms):
        self.spi, self.cs = spi, cs
        self.ticks_ms, self.ticks_diff, self.sleep_ms = ticks_ms, ticks_diff, sleep_ms

    def transaction(self, command, length=0, payload=None):
        self.cs(0)
        try:
            self.spi.write(command)
            if payload is not None:
                self.spi.write(payload)
            return self.spi.read(length, 255) if length else b''
        finally:
            self.cs(1)

    def identity(self):
        return self.transaction(b'\x9f', 3)

    def status(self):
        return self.transaction(b'\x05', 1)[0]

    def ready(self, timeout_ms):
        started = self.ticks_ms()
        while self.status() & 1:
            if self.ticks_diff(self.ticks_ms(), started) >= timeout_ms:
                raise RuntimeError('flash busy timeout; no retry')
            self.sleep_ms(1)

    def enable(self):
        self.ready(500)
        self.transaction(b'\x06')
        if self.status() & 3 != 2:
            raise RuntimeError('write-enable latch not set')

    @staticmethod
    def address(opcode, address):
        return bytes((opcode, address >> 16, (address >> 8) & 255, address & 255))

    def read_sector(self, address):
        if address % 4096 or not 0 <= address <= 0x3ff000:
            raise ValueError('unaligned/out-of-bounds sector')
        self.ready(500)
        return self.transaction(self.address(3, address), 4096)

    def replace_sector(self, address, expected_before, replacement):
        if len(expected_before) != 4096 or len(replacement) != 4096:
            raise ValueError('exactly one sector required')
        if self.identity() != b'\x5e\x40\x16':
            raise RuntimeError('wrong JEDEC ID')
        if self.read_sector(address) != expected_before:
            raise RuntimeError('sector changed before erase')
        if expected_before == replacement:
            return
        self.enable()
        self.transaction(self.address(0x20, address))
        self.ready(3000)
        if self.read_sector(address) != b'\xff' * 4096:
            raise RuntimeError('sector erase readback failed')
        for offset in range(0, 4096, 256):
            page = replacement[offset:offset + 256]
            if page == b'\xff' * 256:
                continue
            self.enable()
            self.transaction(self.address(2, address + offset), payload=page)
            self.ready(500)
        if self.read_sector(address) != replacement:
            raise RuntimeError('program readback failed')


def serve(flash, input_stream, output_stream, hexlify, unhexlify, crc32):
    def emit(line):
        output_stream.write(line + '\n')
    ident = flash.identity()
    if ident != b'\x5e\x40\x16':
        emit('ERROR:wrong JEDEC ID')
        return
    emit('READY:' + hexlify(ident).decode())
    while True:
        line = input_stream.readline()
        if not line:
            return
        try:
            fields = line.strip().split(':')
            if fields == ['QUIT']:
                emit('DONE')
                return
            if fields[0] == 'READ' and len(fields) == 2:
                address = int(fields[1], 16)
                data = flash.read_sector(address)
                emit('DATA:%06x:%s' % (address, hexlify(data).decode()))
            elif fields[0] == 'RESTORE' and len(fields) == 5:
                address = int(fields[1], 16)
                before_crc, after_crc = int(fields[2], 16), int(fields[3], 16)
                data = unhexlify(fields[4])
                if len(data) != 4096 or crc32(data) & 0xffffffff != after_crc:
                    raise ValueError('bad replacement CRC/length')
                before = flash.read_sector(address)
                if crc32(before) & 0xffffffff != before_crc:
                    raise ValueError('preimage CRC differs')
                flash.replace_sector(address, before, data)
                emit('RESTORED:%06x:%08x' % (address, after_crc))
            else:
                raise ValueError('unknown command')
        except Exception as error:
            emit('ERROR:' + str(error))
            return
