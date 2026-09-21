import machine
import utime
import ustruct
import sys

###############################################
# Constants

# I2C address
PCBARTISTS_DBM = 0x48

# Registers
I2C_REG_VERSION		= 0x00
I2C_REG_ID3			= 0x01
I2C_REG_ID2			= 0x02
I2C_REG_ID1			= 0x03
I2C_REG_ID0			= 0x04
I2C_REG_SCRATCH		= 0x05
I2C_REG_CONTROL		= 0x06
I2C_REG_TAVG_HIGH	= 0x07
I2C_REG_TAVG_LOW	= 0x08
I2C_REG_RESET		= 0x09
I2C_REG_DECIBEL		= 0x0A
I2C_REG_MIN			= 0x0B
I2C_REG_MAX			= 0x0C
I2C_REG_THR_MIN     = 0x0D
I2C_REG_THR_MAX     = 0x0E
I2C_REG_HISTORY_0	= 0x14
I2C_REG_HISTORY_99	= 0x77

###############################################
# Settings

# Initialize I2C with pins
i2c = machine.I2C(0,
                  scl=machine.Pin(17),
                  sda=machine.Pin(16),
                  freq=100000)
###############################################
# Functions

def reg_write(i2c, addr, reg, data):
    """
    Write bytes to the specified register.
    """
    
    # Construct message
    msg = bytearray()
    msg.append(data)
    
    # Write out message to register
    i2c.writeto_mem(addr, reg, msg)
    
def reg_read(i2c, addr, reg, nbytes=1):
    """
    Read byte(s) from specified register. If nbytes > 1, read from consecutive
    registers.
    """
    
    # Check to make sure caller is asking for 1 or more bytes
    if nbytes < 1:
        return bytearray()
    
    # Request data from specified register(s) over I2C
    data = i2c.readfrom_mem(addr, reg, nbytes)
    
    return data

###############################################
# Main

# Read device ID to make sure that we can communicate with the ADXL343
data = reg_read(i2c, PCBARTISTS_DBM, I2C_REG_VERSION)
print("dbMeter VERSION = 0x{:02x}".format(int.from_bytes(data, "big")))

data = reg_read(i2c, PCBARTISTS_DBM, I2C_REG_ID3, 4)
print("Unique ID: 0x{:02x} ".format(int.from_bytes(data, "big")))

while True:
    data = reg_read(i2c, PCBARTISTS_DBM, I2C_REG_DECIBEL)
    print("Sound Level (dB SPL) = {:03d}".format(int.from_bytes(data, "big")))
    utime.sleep (2)
sys.exit()
