import platform
import sys

import pyvisa

def check_environment():
    print(f"--- Long Game SDK Environment Diagnostic ---")
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version}")
    
    # Check for library conflicts
    if 'visa' in sys.modules:
        print("WARNING: 'visa' (legacy) module loaded. This may conflict with 'pyvisa'.")
    
    try:
        rm = pyvisa.ResourceManager("@py")
        print(f"PyVISA Backend: {rm.visalib}")
        resources = rm.list_resources()
        print(f"Detected Resources: {resources}")
        for resource in resources:
            try:
                instrument = rm.open_resource(resource)
                instrument.timeout = 3000
                idn = instrument.query("*IDN?").strip().replace("\x00", "")
                print(f"  {resource}: {idn}")
                instrument.close()
            except Exception as e:
                print(f"  {resource}: ID query failed: {e}")
    except Exception as e:
        print(f"VISA Backend Error: {e}")
        print("Recommendation: Ensure pyvisa-py, pyusb, and USB permissions are correctly configured.")

if __name__ == "__main__":
    check_environment()
