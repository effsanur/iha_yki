--- ÇALIŞTIRMA ---

link 1 down 

pkill -9 -f "ardupilot|mavproxy|gz|sim_vehicle|ruby"

TERMİNAL 1

cd
gz sim -v4 -r vtail_runway.sdf

TERMİNAL 2 - SITL 

cd ~/ardupilot/ArduPlane
../Tools/autotest/sim_vehicle.py -v ArduPlane -f gazebo-zephyr --model JSON --map --console




--- SANAL SUNUCU ÇALIŞTIRMA ---

Çalışma dizinine gidin ve sanal ortamı oluşturup bağımlılıkları yükleyin:

cd /home/efsanur/Desktop/iha_yki
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

Sahte yarışma sunucusunu çalıştırın (Terminal 1):

source .venv/bin/activate
python3 sim_sunucu.py
# opsiyonel: python3 sim_sunucu.py --hss-baslangic 30

Sunucu 0.0.0.0:5000 üzerinde çalışır.

YKİ'yi sim modunda başlatın (Terminal 2):

source .venv/bin/activate
./baslat.sh --sim

Uygulama açıldığında sunucu adresi olarak http://127.0.0.1:5000 girin.

(Opsiyonel) Kilit/QR akışını besleyin (Terminal 3):
source .venv/bin/activate
python3 ornekler/jetson_gorev_ornek.py


Giriş bilgileri (README): kullanıcı adı takimkadi, şifre takimsifresi. Arayüzde SUNUCU → GİRİŞ YAP → TELEMETRİYİ BAŞLAT.