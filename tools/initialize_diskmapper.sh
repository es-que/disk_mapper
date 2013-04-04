#!/bin/bash

function parseArgs() {
  # Setup your arguments here.
  while getopts 'v:t:i:g:h' OPTION; do
    case $OPTION in
      v) VBS_PER_DISK=$OPTARG
         ;;
      t) TOTAL_VBS=$OPTARG
         ;;
      i) DISK_MAPPER_IP=$OPTARG
         ;;
      g) GAME_ID=$OPTARG
         ;;
      h) usage
         exit 0
         ;;
      *) echo 'Invalid option.'
         usage
         exit 3
        ;;
    esac
  done

  if [[ -z $VBS_PER_DISK || -z $TOTAL_VBS || -z $DISK_MAPPER_IP || -z $GAME_ID ]]; then
    usage
    exit 1
  fi
}

function usage() {
  # Output script usage.
  cat << EOF
  Usage: ${0##*/} OPTIONS

  OPTIONS:
    -i  Disk Mapper IP.
    -g  Game ID.
    -v  Number of vbuckets per disk on the storage server.
    -t  Total number of vbuckets in the entire pool.
    -h  Show this message.
EOF
}

function main() {
  parseArgs $@
  vb_id=0
  vb_group_id=0
  vb_group_count=$(($TOTAL_VBS / $VBS_PER_DISK))
  echo valid > /tmp/dm_init_emp_file

  while [ $vb_group_id -lt $vb_group_count ] ; do

    actual_url=$(curl -sf --connect-timeout 15 --max-time 120 --request POST http://$DISK_MAPPER_IP/api/$GAME_ID/vb_group_$vb_group_id/)
    i=0
    while [ $i -lt $VBS_PER_DISK ] ; do
      
      curl -sf -L --connect-timeout 15 --max-time 600 --request POST --data-binary @/tmp/dm_init_emp_file $actual_url/vb_$vb_id/valid
      let vb_id=vb_id+1
      let i=i+1
    done
    let vb_group_id=vb_group_id+1
  done

}

# We don't want to call main, if this file is being sourced.
if [[ $BASH_SOURCE == $0 ]]; then
  main $@
fi
