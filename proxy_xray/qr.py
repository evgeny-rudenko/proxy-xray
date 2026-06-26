GF_EXP = [0] * 512
GF_LOG = [0] * 256


def init_tables():
    value = 1
    for i in range(255):
        GF_EXP[i] = value
        GF_LOG[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        GF_EXP[i] = GF_EXP[i - 255]


init_tables()


def gf_mul(a, b):
    if not a or not b:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def rs_generator(degree):
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        factor = GF_EXP[i]
        for j, coefficient in enumerate(poly):
            next_poly[j] ^= coefficient
            next_poly[j + 1] ^= gf_mul(coefficient, factor)
        poly = next_poly
    return poly


def rs_remainder(data, degree):
    generator = rs_generator(degree)
    remainder = [0] * degree
    for value in data:
        factor = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(degree):
            remainder[i] ^= gf_mul(generator[i + 1], factor)
    return remainder


def bits_from_int(value, width):
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def make_data_codewords(text, data_codewords=136):
    payload = text.encode("utf-8")
    if len(payload) > data_codewords - 3:
        raise ValueError("connection string is too long for QR v6-L")

    bits = []
    bits.extend(bits_from_int(0b0100, 4))
    bits.extend(bits_from_int(len(payload), 8))
    for byte in payload:
        bits.extend(bits_from_int(byte, 8))
    bits.extend([0] * min(4, data_codewords * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    codewords = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i : i + 8]:
            value = (value << 1) | bit
        codewords.append(value)

    pad = (0xEC, 0x11)
    index = 0
    while len(codewords) < data_codewords:
        codewords.append(pad[index % 2])
        index += 1
    return codewords


def interleave_blocks(data):
    block_size = 68
    ecc_size = 18
    blocks = [data[:block_size], data[block_size:]]
    ecc_blocks = [rs_remainder(block, ecc_size) for block in blocks]
    result = []
    for i in range(block_size):
        for block in blocks:
            result.append(block[i])
    for i in range(ecc_size):
        for block in ecc_blocks:
            result.append(block[i])
    return result


def empty_matrix(size):
    return [[False for _ in range(size)] for _ in range(size)], [[False for _ in range(size)] for _ in range(size)]


def set_module(matrix, reserved, x, y, value, reserve=True):
    if 0 <= x < len(matrix) and 0 <= y < len(matrix):
        matrix[y][x] = bool(value)
        if reserve:
            reserved[y][x] = True


def add_finder(matrix, reserved, x, y):
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            xx = x + dx
            yy = y + dy
            if not (0 <= xx < len(matrix) and 0 <= yy < len(matrix)):
                continue
            dark = (
                0 <= dx <= 6
                and 0 <= dy <= 6
                and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
            )
            set_module(matrix, reserved, xx, yy, dark)


def add_alignment(matrix, reserved, center_x, center_y):
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            distance = max(abs(dx), abs(dy))
            set_module(matrix, reserved, center_x + dx, center_y + dy, distance in (0, 2))


def add_function_patterns(matrix, reserved, version=6):
    size = len(matrix)
    add_finder(matrix, reserved, 0, 0)
    add_finder(matrix, reserved, size - 7, 0)
    add_finder(matrix, reserved, 0, size - 7)
    add_alignment(matrix, reserved, 34, 34)

    for i in range(8, size - 8):
        set_module(matrix, reserved, i, 6, i % 2 == 0)
        set_module(matrix, reserved, 6, i, i % 2 == 0)

    set_module(matrix, reserved, 8, 4 * version + 9, True)

    for i in range(9):
        if i != 6:
            set_module(matrix, reserved, 8, i, False)
            set_module(matrix, reserved, i, 8, False)
    for i in range(8):
        set_module(matrix, reserved, size - 1 - i, 8, False)
        set_module(matrix, reserved, 8, size - 1 - i, False)


def mask_bit(x, y):
    return (x + y) % 2 == 0


def add_data(matrix, reserved, codewords):
    bits = []
    for codeword in codewords:
        bits.extend(bits_from_int(codeword, 8))

    size = len(matrix)
    bit_index = 0
    direction = -1
    y = size - 1
    x = size - 1
    while x > 0:
        if x == 6:
            x -= 1
        while 0 <= y < size:
            for dx in (0, 1):
                xx = x - dx
                if reserved[y][xx]:
                    continue
                bit = bits[bit_index] if bit_index < len(bits) else 0
                if mask_bit(xx, y):
                    bit ^= 1
                set_module(matrix, reserved, xx, y, bool(bit), reserve=False)
                bit_index += 1
            y += direction
        direction = -direction
        y += direction
        x -= 2


def format_bits(ec_level=0b01, mask=0):
    data = (ec_level << 3) | mask
    value = data << 10
    generator = 0x537
    for shift in range(14, 9, -1):
        if (value >> shift) & 1:
            value ^= generator << (shift - 10)
    return ((data << 10) | value) ^ 0x5412


def add_format_bits(matrix, reserved, bits):
    size = len(matrix)
    for i in range(15):
        bit = ((bits >> i) & 1) == 1
        if i < 6:
            set_module(matrix, reserved, 8, i, bit)
        elif i < 8:
            set_module(matrix, reserved, 8, i + 1, bit)
        else:
            set_module(matrix, reserved, 8, size - 15 + i, bit)

        if i < 8:
            set_module(matrix, reserved, size - i - 1, 8, bit)
        elif i == 8:
            set_module(matrix, reserved, 7, 8, bit)
        else:
            set_module(matrix, reserved, 14 - i, 8, bit)


def qr_matrix(text):
    size = 41
    matrix, reserved = empty_matrix(size)
    add_function_patterns(matrix, reserved)
    codewords = interleave_blocks(make_data_codewords(text))
    add_data(matrix, reserved, codewords)
    add_format_bits(matrix, reserved, format_bits())
    return matrix


def qr_svg(text, scale=7, border=4):
    matrix = qr_matrix(text)
    size = len(matrix)
    canvas = (size + border * 2) * scale
    rects = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                rects.append(
                    f'<rect x="{(x + border) * scale}" y="{(y + border) * scale}" width="{scale}" height="{scale}"/>'
                )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}" '
        'role="img" aria-label="VLESS connection QR code">'
        '<rect width="100%" height="100%" fill="#fff"/>'
        f'<g fill="#111820">{"".join(rects)}</g>'
        "</svg>"
    )
