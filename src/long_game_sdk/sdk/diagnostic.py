import sys
import pyvisa
import platform

def check_environment():
    print(f"--- Long Game SDK Environment Diagnostic ---")
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version}")
    
    # Check for library conflicts
    if 'visa' in sys.modules:
        print("WARNING: 'visa' (legacy) module loaded. This may conflict with 'pyvisa'.")
    
    try:
        rm = pyvisa.ResourceManager()
        print(f"PyVISA Backend: {rm.visalib}")
        resources = rm.list_resources()
        print(f"Detected Resources: {resources}")
    except Exception as e:
        print(f"VISA Backend Error: {e}")
        print("Recommendation: Ensure NI-VISA or pyvisa-py is correctly installed.")

if __name__ == "__main__":
    check_environment()
