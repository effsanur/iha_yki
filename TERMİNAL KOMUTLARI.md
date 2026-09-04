--- ÇALIŞTIRMA ---

link 1 down 

pkill -9 -f "ardupilot|mavproxy|gz|sim_vehicle|ruby"

TERMİNAL 1

cd
gz sim -v4 -r vtail_runway.sdf

TERMİNAL 2 - SITL 

cd ~/ardupilot/ArduPlane
../Tools/autotest/sim_vehicle.py -v ArduPlane -f gazebo-zephyr --model JSON --map --console
