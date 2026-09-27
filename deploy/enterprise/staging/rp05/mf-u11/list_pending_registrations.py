"""Read-only listing, no DELETE/PUT/POST operations, default print-only."""
import json
import urllib.parse
from realm_admin import Client, parser


def pending(client):
    root='realms/mentorflow'
    groups=client.call(root+'/groups?search=self-registered-learners-e109&exact=true')
    groups=[g for g in groups if g.get('name')=='self-registered-learners-e109']
    legacy=set()
    for group in groups:
        for offset in range(0,100000,100):
            rows=client.call(root+'/groups/'+urllib.parse.quote(group['id'],safe='')+'/members?briefRepresentation=false&first='+str(offset)+'&max=100')
            legacy.update(row['id'] for row in rows)
            if len(rows)<100:break
        else: raise ValueError('group pagination limit reached; listing incomplete')
    for offset in range(0,100000,100):
        rows=client.call(root+'/users?briefRepresentation=false&first='+str(offset)+'&max=100')
        for row in rows:
            if row.get('emailVerified') or row.get('serviceAccountClientId') or row.get('federationLink'):continue
            marker=row.get('attributes',{}).get('mf.registration.v1',[])
            marked='pending' in marker
            if not marked and row['id'] not in legacy:continue
            # Legacy group membership is evidence for review, not proof of origin.
            yield {key:row.get(key) for key in ('id','username','email','emailVerified','createdTimestamp')} | {
                'provenance':'MF-U11 self-registration marker' if marked else 'legacy group; origin requires human review',
                'action':'NONE; never deleted by this script'}
        if len(rows)<100:return
    raise ValueError('user pagination limit reached; listing incomplete')


def main():
    args=parser().parse_args()
    if not args.execute:
        print('DRY RUN: GET pending self-registration users; print only, never delete');return
    client=Client(args.origin,args.ca_file,args.token_file)
    for row in pending(client):print(json.dumps(row,ensure_ascii=False))


if __name__=='__main__':main()
