name: Signal Bots Scheduler

on:
  schedule:
    - cron: '0 9,11,13,15,17,19,21,23 * * *'  # Every 2 hours UTC (10am-12am UK)
  workflow_dispatch:

jobs:
  run-bots:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      TZ: Europe/London

    steps:
    - name: Checkout code
      uses: actions/checkout@v4
      
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        
    - name: Install system dependencies
      run: |
        sudo apt-get update
        sudo apt-get install -y chromium-browser chromium-driver python3-dev \
        build-essential autoconf libtool pkg-config wget
        
    - name: Build TA-Lib from source with robust fixes
      run: |
        wget https://downloads.sourceforge.net/project/ta-lib/ta-lib/0.4.0/ta-lib-0.4.0-src.tar.gz
        tar -xzf ta-lib-0.4.0-src.tar.gz
        cd ta-lib
        
        # Apply comprehensive build fixes
        sed -i 's/--no-preserve=mode/--preserve=mode/' src/tools/gen_code/Makefile.*
        sed -i 's/-Wl,--no-as-needed//' src/Makefile.*
        sed -i 's|AM_CPPFLAGS = -I$(top_srcdir)/include|AM_CPPFLAGS = -I$(top_srcdir)/include -I$(top_srcdir)/src/ta_common|' src/tools/gen_code/Makefile.*
        
        # Fix header paths for gen_code
        sed -i 's|"ta_defs.h"|"ta_common/ta_defs.h"|g' src/tools/gen_code/gen_code.c
        sed -i 's|"ta_func.h"|"ta_func/ta_func.h"|g' src/tools/gen_code/gen_code.c
        
        ./configure --prefix=/usr
        make -j1
        sudo make install
        cd ..
        sudo ldconfig
        
    - name: Install Python dependencies
      run: |
        python -m pip install --upgrade pip
        pip install wheel
        # Install pinned NumPy version first
        pip install "numpy==1.23.5"
        # Install TA-Lib from PyPI instead of GitHub
        pip install --no-cache-dir TA-Lib
        # Install other requirements
        pip install -r requirements.txt
        
    - name: Verify installations
      run: |
        python -c "import talib; print(f'TA-Lib {talib.__version__} installed successfully')"
        python -c "import numpy; print(f'NumPy {numpy.__version__} installed')"
        
    - name: Run Commodity Signal Bot
      env:
        TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      run: python Commodity_Signal_Bot.py
      
    - name: Run Stock Signal Bot
      env:
        TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      run: python Stock_signal_Bot.py
  
    - name: Run Forex Signal Bot
      env:
        TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
      run: python Forex_Signal_Bot.py
      
    - name: Run Free Forex Signal
      env:
        TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      run: python Free_Forex_Signal.py
